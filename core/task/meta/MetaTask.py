import threading
import time
from pathlib import Path
from typing import Optional, Dict
from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger

from core.task.base import BaseTask, wrap_run, TaskCompletion
from core.actor.base import Actor
from core.llm.token_logger import llm_tag
from core.trace import record_actor_trace, snapshot_row
from core.evidence import submit_with_evidence
from core.utils import load_dataset


class MetaTask(BaseTask):

    def __init__(
            self,
            open_parallel: bool = True,
            max_workers: int = 3,
            actor: Actor = None,
            actor_args: Dict = None,
            is_save: bool = True,
            checkpoint_config: Optional[Dict] = None,
            resume_state = None,
            **kwargs
    ):
        super().__init__(**kwargs)

        self.open_parallel: bool = open_parallel
        self.max_workers: int = max_workers
        self.is_save: bool = self.is_save_dataset if is_save is None else is_save

        self.actor: Actor = self.load_actor(**actor_args if actor_args else {}) if actor is None else actor
        self._checkpoint_config: Dict = checkpoint_config or {}
        self._resume_state = resume_state
        self._checkpoint_lock = threading.Lock()
        self._checkpoint_flush_lock = threading.Lock()
        self._checkpoint_counter = 0

    @property
    def _checkpoint_enabled(self) -> bool:
        return bool(self._resume_state) and self._checkpoint_config.get("enabled", True)

    @property
    def _checkpoint_interval(self) -> int:
        try:
            interval = int(self._checkpoint_config.get("interval", 50))
        except (TypeError, ValueError):
            interval = 50
        return max(1, interval)

    def _checkpoint_paths(self, stage_name: str) -> tuple[Optional[Path], Optional[Path]]:
        state_path = self._checkpoint_config.get("state_path")
        datasets_dir = self._checkpoint_config.get("datasets_dir")
        if not state_path or not datasets_dir:
            return None, None
        return Path(state_path), Path(datasets_dir) / f"{stage_name}.json"

    def _load_checkpoint_dataset(self, dataset_ckpt_path: Optional[Path], actor: Actor) -> None:
        if not dataset_ckpt_path or not dataset_ckpt_path.exists():
            return
        ckpt_rows = load_dataset(dataset_ckpt_path)
        if isinstance(ckpt_rows, list) and self.dataset is not None and len(ckpt_rows) == len(self.dataset):
            self.dataset._dataset = ckpt_rows
            actor.dataset = self.dataset
        elif isinstance(ckpt_rows, list) and self.dataset is not None:
            logger.warning(
                f"checkpoint dataset row count mismatch, skipped: {dataset_ckpt_path} "
                f"(checkpoint={len(ckpt_rows)}, current={len(self.dataset)})"
            )

    def _checkpoint_flush(
            self,
            state_path: Optional[Path],
            dataset_ckpt_path: Optional[Path],
            dataset=None,
    ) -> None:
        if not self._checkpoint_enabled:
            return
        flush_lock = getattr(self, "_checkpoint_flush_lock", None)
        if flush_lock is None:
            flush_lock = threading.Lock()
            self._checkpoint_flush_lock = flush_lock
        with flush_lock:
            if state_path and self._checkpoint_config.get("save_state", True):
                self._resume_state.save(state_path)
            if dataset_ckpt_path:
                ds = dataset or self.dataset
                dataset_ckpt_path.parent.mkdir(parents=True, exist_ok=True)
                ds.save_data(dataset_ckpt_path)

    def _mark_checkpoint_progress(self, stage_name: str, instance_id: str) -> bool:
        with self._checkpoint_lock:
            self._resume_state.mark_completed(stage_name, instance_id)
            self._checkpoint_counter += 1
            if self._checkpoint_counter >= self._checkpoint_interval:
                self._checkpoint_counter = 0
                return True
        return False

    @wrap_run
    def run(self):
        actor = self.actor
        if actor is None:
            logger.warning("Actor 未初始化，跳过任务执行")
            return

        logger.info(f"开始执行 MetaTask: {self.name} ({self.task_id}), 数据集大小: {len(self.dataset)}")
        stage_name = self.task_id
        state_path, dataset_ckpt_path = self._checkpoint_paths(stage_name)
        if self._checkpoint_enabled:
            self._load_checkpoint_dataset(dataset_ckpt_path, actor)

        def safe_act(index):
            ins_id = actor.dataset[index]['instance_id']
            if self._checkpoint_enabled and self._resume_state.is_completed(stage_name, ins_id):
                return None
            data_logger = self._task_log.generate_data_logger(ins_id)
            try:
                data_logger.info(f"开始处理样本 {ins_id}")
                before_row = snapshot_row(actor.dataset, index)
                t0 = time.monotonic()
                with llm_tag(f"sample:{ins_id}"):
                    result = actor.act(index, data_logger=data_logger)
                elapsed_s = round(time.monotonic() - t0, 3)
                actor.dataset[index]["_act_elapsed_s"] = elapsed_s
                record_actor_trace(
                    dataset=actor.dataset,
                    item=index,
                    actor=actor,
                    result=result,
                    elapsed_s=elapsed_s,
                    before_row=before_row,
                    data_logger=data_logger,
                    stage_name=stage_name,
                )
                data_logger.info(f"样本 {ins_id} 处理完成 ({elapsed_s}s)")
                if self._checkpoint_enabled:
                    if self._mark_checkpoint_progress(stage_name, ins_id):
                        self._checkpoint_flush(state_path, dataset_ckpt_path, dataset=actor.dataset)
                return index, result
            except Exception as e:
                error_info = f"Error occurred while executing act() on sample {ins_id}: {e}."
                data_logger.info(error_info)
                # Log error info in dataset and task log
                row = self.dataset[index]
                row["error_info"] = error_info
                record_actor_trace(
                    dataset=self.dataset,
                    item=index,
                    actor=actor,
                    error=e,
                    before_row=before_row if "before_row" in locals() else snapshot_row(self.dataset, index),
                    data_logger=data_logger,
                    stage_name=stage_name,
                )
                self._task_log.add_error_data(row)
                self._task_log.error(error_info)
                return None
            finally:
                data_logger.save()

        results = {}
        skipped = 0
        if self._checkpoint_enabled:
            completed_ids = self._resume_state.completed_ids(stage_name)
            dataset_ids = {str(row.get("instance_id")) for row in actor.dataset}
            skipped = len(completed_ids & dataset_ids)
            if skipped:
                logger.info(f"跳过 {skipped} 个已完成样本 (resume)")
        logger.info(f"使用线程池执行任务，最大工作线程数: {self.max_workers}")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {submit_with_evidence(executor, safe_act, i): i for i in range(len(self.dataset))}
            completed_count = skipped
            for future in as_completed(futures):
                res = future.result()
                if res is not None:
                    completed_count += 1
                if completed_count % 10 == 0 or completed_count == len(self.dataset):
                    logger.info(f"任务进度: {completed_count}/{len(self.dataset)} ({completed_count/len(self.dataset)*100:.1f}%)")
                if res is not None:
                    idx, val = res
                    results[idx] = val
        if self._checkpoint_enabled:
            self._checkpoint_flush(state_path, dataset_ckpt_path, dataset=actor.dataset)

        logger.info(f"MetaTask 执行完成: {self.name} ({self.task_id}), 成功处理: {len(results)}/{len(self.dataset)} 个样本")
        self.dataset = actor.dataset

        if self.is_save:
            # For ComplexTask, actor.dataset and task.dataset may differ
            logger.info(f"保存任务结果到: {self.dataset_save_path}")
            self.save(self.dataset_save_path)

        return TaskCompletion(results)

    @abstractmethod
    def load_actor(self, actor_type: str = None, **kwargs) -> Optional[Actor]:
        pass
