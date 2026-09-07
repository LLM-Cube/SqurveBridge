import copy
import inspect
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Union, List, Optional
from llama_index.core.llms.llm import LLM
from loguru import logger

from core.actor.base import Actor
from core.actor.nest.pipeline import PipelineActor
from core.data_manage import update_dataset
from core.llm.token_logger import llm_tag
from core.trace import record_actor_trace, snapshot_row
from core.evidence import submit_with_evidence
from core.task.base import TaskCompletion
from core.task.meta.MetaTask import MetaTask


class _StageActorDatasetView:
    """Per-worker actor view that keeps dataset writes off the shared stage actor."""

    def __init__(self, actor: Actor, dataset):
        object.__setattr__(self, "_actor", actor)
        object.__setattr__(self, "dataset", dataset)

    def __getattr__(self, name):
        actor = object.__getattribute__(self, "_actor")
        descriptor = inspect.getattr_static(type(actor), name, None)
        if isinstance(descriptor, staticmethod):
            return descriptor.__get__(actor, type(actor))
        if isinstance(descriptor, classmethod):
            return descriptor.__get__(type(actor), type(actor))
        if callable(descriptor) and hasattr(descriptor, "__get__"):
            return descriptor.__get__(self, type(self))
        return getattr(actor, name)

    def __setattr__(self, name, value):
        if name in {"_actor", "dataset"}:
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_actor"), name, value)

    def act(self, *args, **kwargs):
        actor = object.__getattribute__(self, "_actor")
        return type(actor).act(self, *args, **kwargs)


class ComplexTask(MetaTask):
    # todo 后续对 Complex 补充更多日志记录等方法
    NAME = "ComplexTask"

    def __init__(
            self,
            llm: Union[LLM, List[LLM]] = None,
            pipeline_run_mode: str = "sample",
            **kwargs
    ):
        super().__init__(**kwargs)
        self.llm: Union[LLM, List[LLM]] = llm
        self.pipeline_run_mode: str = pipeline_run_mode

    def load_actor(self, actor_type: str = None, **kwargs) -> Optional[Actor]:
        # ComplexTask relies entirely on the actor object being provided externally.
        if hasattr(self, "actor"):
            return self.actor

        return None

    def _uses_stage_checkpoints(self, pipeline: PipelineActor) -> bool:
        return any(getattr(actor, "stage_dataset_save_path", None) for actor in pipeline.actors)

    def _use_staged_run(self, actor: Actor) -> bool:
        return (
            self.pipeline_run_mode == "stage"
            and isinstance(actor, PipelineActor)
            and self._uses_stage_checkpoints(actor)
        )

    def _stage_name(self, stage_actor: Actor) -> str:
        return getattr(
            stage_actor,
            "stage_checkpoint_name",
            getattr(stage_actor, "name", stage_actor.__class__.__name__),
        )

    def _load_resume_dataset(self, actor: PipelineActor) -> None:
        """Restore the most advanced stage checkpoint available."""
        if not self._checkpoint_enabled:
            return
        for stage_actor in reversed(actor.actors):
            _, dataset_ckpt_path = self._checkpoint_paths(self._stage_name(stage_actor))
            if dataset_ckpt_path and dataset_ckpt_path.exists():
                self._load_checkpoint_dataset(dataset_ckpt_path, stage_actor)
                actor.dataset = stage_actor.dataset
                self.dataset = actor.dataset
                logger.info(f"从 stage checkpoint 恢复 dataset: {dataset_ckpt_path}")
                return
        state_path, full_ckpt_path = self._checkpoint_paths(self.task_id)
        if full_ckpt_path and full_ckpt_path.exists():
            self._load_checkpoint_dataset(full_ckpt_path, actor)
            self.dataset = actor.dataset
            if state_path:
                logger.info(f"从 pipeline checkpoint 恢复 dataset: {full_ckpt_path}")

    def _save_sample_stage_snapshots(self, actor: PipelineActor, stage_snapshots: dict[str, list]) -> None:
        """Persist stage-specific row snapshots collected during sample-mode pipeline runs."""
        current_rows = self._dataset_rows(actor.dataset)
        rows_attr = self._row_storage_attr(actor.dataset)
        if rows_attr is None:
            logger.warning("sample stage snapshots skipped: dataset row storage is unavailable")
            return
        for stage_actor in actor.actors:
            checkpoint = getattr(stage_actor, "stage_dataset_save_path", None)
            if not checkpoint:
                continue
            stage_name = self._stage_name(stage_actor)
            rows = stage_snapshots.get(stage_name)
            if rows is None:
                continue
            rows = [
                copy.deepcopy(row) if row is not None else copy.deepcopy(current_rows[index])
                for index, row in enumerate(rows)
            ]
            original_rows = getattr(actor.dataset, rows_attr)
            try:
                setattr(actor.dataset, rows_attr, rows)
                actor.dataset.save_data(checkpoint)
            finally:
                setattr(actor.dataset, rows_attr, original_rows)
            logger.info(f"阶段 snapshot 已保存: {checkpoint}")

    def _dataset_rows(self, dataset) -> list:
        return getattr(dataset, "_dataset", getattr(dataset, "_rows", []))

    def _row_storage_attr(self, dataset) -> Optional[str]:
        if hasattr(dataset, "_dataset"):
            return "_dataset"
        if hasattr(dataset, "_rows"):
            return "_rows"
        return None

    def _worker_stage_actor(self, stage_actor: Actor, dataset) -> Actor:
        return _StageActorDatasetView(stage_actor, dataset)

    def _run_sample_with_stage_checkpoints(self, actor: PipelineActor) -> TaskCompletion:
        """Run each sample through all pipeline stages with per-stage checkpoint state."""
        dataset = actor.dataset
        if not dataset or not actor.actors:
            logger.warning("ComplexTask sample checkpoint run skipped: missing dataset or actors")
            return super().run()

        self._load_resume_dataset(actor)
        dataset = actor.dataset
        stage_size = len(dataset)
        logger.info(
            f"ComplexTask 逐样本执行(子 stage checkpoint): {self.name} ({self.task_id}), "
            f"{len(actor.actors)} stages, 数据集大小: {stage_size}"
        )

        results = {}
        stage_snapshots = {self._stage_name(stage_actor): [None] * stage_size for stage_actor in actor.actors}
        skipped = 0
        if self._checkpoint_enabled:
            completed_full = self._resume_state.completed_ids(self.task_id)
            dataset_ids = {str(row.get("instance_id")) for row in dataset}
            skipped = len(completed_full & dataset_ids)
            if skipped:
                logger.info(f"跳过 {skipped} 个已完成样本 (resume @ {self.task_id})")

        state_path, _ = self._checkpoint_paths(self.task_id)

        def safe_act(index):
            ins_id = str(actor.dataset[index]["instance_id"])
            if self._checkpoint_enabled and self._resume_state.is_completed(self.task_id, ins_id):
                return None

            data_logger = self._task_log.generate_data_logger(ins_id)
            try:
                data_logger.info(f"开始处理样本 {ins_id}")
                t0 = time.monotonic()
                last_result = None
                ds = actor.dataset
                for stage_actor in actor.actors:
                    stage_name = self._stage_name(stage_actor)
                    if self._checkpoint_enabled and self._resume_state.is_completed(stage_name, ins_id):
                        continue

                    stage_dataset = update_dataset(ds, stage_actor.dataset)
                    worker_stage_actor = self._worker_stage_actor(stage_actor, stage_dataset)
                    ds = worker_stage_actor.dataset
                    data_logger.info(f"样本 {ins_id} @ {stage_name}")
                    before_row = snapshot_row(ds, index)
                    stage_t0 = time.monotonic()
                    with llm_tag(f"sample:{ins_id}"):
                        last_result = worker_stage_actor.act(index, data_logger=data_logger)
                    record_actor_trace(
                        dataset=worker_stage_actor.dataset,
                        item=index,
                        actor=worker_stage_actor,
                        result=last_result,
                        elapsed_s=round(time.monotonic() - stage_t0, 3),
                        before_row=before_row,
                        data_logger=data_logger,
                        stage_name=stage_name,
                    )

                    stage_row = ds[index]
                    stage_snapshots[stage_name][index] = copy.deepcopy(stage_row)
                    pipeline_row = actor.dataset[index]
                    if pipeline_row is not stage_row:
                        pipeline_row.clear()
                        pipeline_row.update(copy.deepcopy(stage_row))
                    if last_result is None or stage_row.get("error_info"):
                        data_logger.info(f"样本 {ins_id} @ {stage_name} 失败，跳过后续 stage")
                        return None

                    if self._checkpoint_enabled:
                        _, stage_ckpt_path = self._checkpoint_paths(stage_name)
                        if self._mark_checkpoint_progress(stage_name, ins_id):
                            self._checkpoint_flush(state_path, stage_ckpt_path, dataset=ds)

                elapsed_s = round(time.monotonic() - t0, 3)
                actor.dataset[index]["_act_elapsed_s"] = elapsed_s
                data_logger.info(f"样本 {ins_id} 处理完成 ({elapsed_s}s)")

                if self._checkpoint_enabled:
                    _, full_ckpt_path = self._checkpoint_paths(self.task_id)
                    if self._mark_checkpoint_progress(self.task_id, ins_id):
                        self._checkpoint_flush(state_path, full_ckpt_path, dataset=actor.dataset)

                return index, last_result
            except Exception as exc:
                error_info = f"Error occurred while executing act() on sample {ins_id}: {exc}."
                data_logger.info(error_info)
                row = actor.dataset[index]
                row["error_info"] = error_info
                record_actor_trace(
                    dataset=actor.dataset,
                    item=index,
                    actor=actor,
                    error=exc,
                    before_row=snapshot_row(actor.dataset, index),
                    data_logger=data_logger,
                    stage_name=self.task_id,
                )
                self._task_log.add_error_data(row)
                self._task_log.error(error_info)
                return None
            finally:
                data_logger.save()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {submit_with_evidence(executor, safe_act, i): i for i in range(stage_size)}
            completed_count = skipped
            for future in as_completed(futures):
                res = future.result()
                if res is not None:
                    completed_count += 1
                if completed_count % 10 == 0 or completed_count == stage_size:
                    logger.info(
                        f"任务进度: {completed_count}/{stage_size} "
                        f"({completed_count / stage_size * 100:.1f}%)"
                    )
                if res is not None:
                    idx, val = res
                    results[idx] = val

        self.dataset = actor.dataset
        if self._checkpoint_enabled:
            _, full_ckpt_path = self._checkpoint_paths(self.task_id)
            self._checkpoint_flush(state_path, full_ckpt_path, dataset=actor.dataset)

        self._save_sample_stage_snapshots(actor, stage_snapshots)
        logger.info(
            f"ComplexTask 逐样本执行完成: {self.name} ({self.task_id}), "
            f"成功处理: {len(results)}/{stage_size} 个样本"
        )

        if self.is_save:
            logger.info(f"保存任务结果到: {self.dataset_save_path}")
            self.save(self.dataset_save_path)

        return TaskCompletion(results)

    def run(self):
        """Dispatch ComplexTask through one of three pipeline execution paths.

        Path 1: stage mode. When ``pipeline_run_mode == "stage"`` and the actor is
        a PipelineActor whose child actors expose ``stage_dataset_save_path``, each
        pipeline stage processes the whole dataset before the next stage starts.
        This gives the best checkpoint granularity/performance tradeoff for large
        runs because every stage has its own durable snapshot and resume state.

        Path 2: sample mode with child stage checkpoints. When the actor is the
        same checkpoint-enabled PipelineActor but ``pipeline_run_mode`` is not
        ``"stage"``, each sample runs through the full stage chain before the next
        sample result is considered complete. This gives the finest resume state
        across sub-stages, but it has more per-sample bookkeeping overhead.

        Path 3: MetaTask fallback. Non-checkpointed PipelineActors and regular
        actors delegate to ``MetaTask.run()``. That path still runs samples through
        the task executor, but it does not persist per-stage snapshots for
        ``stage_eval``.
        """
        actor = self.actor
        if self._use_staged_run(actor):
            return self._run_staged(actor)

        if isinstance(actor, PipelineActor) and self._uses_stage_checkpoints(actor):
            return self._run_sample_with_stage_checkpoints(actor)

        if isinstance(actor, PipelineActor) and actor.actors:
            logger.info(
                f"ComplexTask 逐样本执行: {self.name} ({self.task_id}), "
                f"{len(actor.actors)} stages, 数据集大小: {len(actor.dataset)}"
            )
        return super().run()

    def _run_staged(self, actor: PipelineActor) -> TaskCompletion:
        dataset = actor.dataset
        if not dataset or not actor.actors:
            logger.warning("ComplexTask staged run skipped: missing dataset or actors")
            return super().run()

        logger.info(
            f"ComplexTask 分阶段执行: {self.name} ({self.task_id}), "
            f"{len(actor.actors)} stages, 数据集大小: {len(dataset)}"
        )
        results = {}

        for stage_idx, stage_actor in enumerate(actor.actors):
            stage_actor.dataset = update_dataset(dataset, stage_actor.dataset)
            stage_dataset = stage_actor.dataset
            stage_size = len(stage_dataset)
            stage_name = self._stage_name(stage_actor)
            display_name = getattr(stage_actor, "name", stage_actor.__class__.__name__)
            logger.info(
                f"阶段 {stage_idx + 1}/{len(actor.actors)}: {display_name}, "
                f"样本数: {stage_size}"
            )
            state_path, dataset_ckpt_path = self._checkpoint_paths(stage_name)
            if self._checkpoint_enabled:
                self._load_checkpoint_dataset(dataset_ckpt_path, stage_actor)
                stage_dataset = stage_actor.dataset
                stage_size = len(stage_dataset)
                completed_ids = self._resume_state.completed_ids(stage_name)
                dataset_ids = {str(row.get("instance_id")) for row in stage_dataset}
                skipped = len(completed_ids & dataset_ids)
                if skipped:
                    logger.info(f"[{display_name}] 跳过 {skipped} 个已完成样本 (resume)")
            else:
                completed_ids = set()
                skipped = 0

            def safe_act(index, _stage_actor=stage_actor, _stage_name=stage_name):
                ins_id = _stage_actor.dataset[index]["instance_id"]
                if self._checkpoint_enabled and ins_id in completed_ids:
                    return None
                data_logger = self._task_log.generate_data_logger(ins_id)
                try:
                    data_logger.info(f"开始处理样本 {ins_id} @ {_stage_name}")
                    before_row = snapshot_row(_stage_actor.dataset, index)
                    t0 = time.monotonic()
                    with llm_tag(f"sample:{ins_id}"):
                        result = _stage_actor.act(index, data_logger=data_logger)
                    record_actor_trace(
                        dataset=_stage_actor.dataset,
                        item=index,
                        actor=_stage_actor,
                        result=result,
                        elapsed_s=round(time.monotonic() - t0, 3),
                        before_row=before_row,
                        data_logger=data_logger,
                        stage_name=_stage_name,
                    )
                    data_logger.info(f"样本 {ins_id} 处理完成 @ {_stage_name}")
                    if self._checkpoint_enabled:
                        if self._mark_checkpoint_progress(_stage_name, ins_id):
                            self._checkpoint_flush(
                                state_path,
                                dataset_ckpt_path,
                                dataset=_stage_actor.dataset,
                            )
                    return index, result
                except Exception as exc:
                    error_info = (
                        f"Error occurred while executing {_stage_name} on sample {ins_id}: {exc}."
                    )
                    data_logger.info(error_info)
                    row = _stage_actor.dataset[index]
                    row["error_info"] = error_info
                    record_actor_trace(
                        dataset=_stage_actor.dataset,
                        item=index,
                        actor=_stage_actor,
                        error=exc,
                        before_row=before_row if "before_row" in locals() else snapshot_row(_stage_actor.dataset, index),
                        data_logger=data_logger,
                        stage_name=_stage_name,
                    )
                    self._task_log.add_error_data(row)
                    self._task_log.error(error_info)
                    return None
                finally:
                    data_logger.save()

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {submit_with_evidence(executor, safe_act, i): i for i in range(stage_size)}
                completed_count = skipped
                for future in as_completed(futures):
                    res = future.result()
                    if res is not None:
                        completed_count += 1
                    if completed_count % 10 == 0 or completed_count == stage_size:
                        logger.info(
                            f"{display_name} 进度: {completed_count}/{stage_size} "
                            f"({completed_count / stage_size * 100:.1f}%)"
                        )
                    if res is not None:
                        idx, val = res
                        results[idx] = val

            dataset = stage_actor.dataset
            if self._checkpoint_enabled:
                self._checkpoint_flush(state_path, dataset_ckpt_path, dataset=dataset)
            checkpoint = getattr(stage_actor, "stage_dataset_save_path", None)
            if checkpoint:
                dataset.save_data(checkpoint)
                logger.info(f"阶段检查点已保存: {checkpoint}")

        actor.dataset = dataset
        self.dataset = dataset
        logger.info(
            f"ComplexTask 分阶段执行完成: {self.name} ({self.task_id}), "
            f"成功处理: {len(results)}/{len(dataset)} 个样本"
        )

        if self.is_save:
            logger.info(f"保存任务结果到: {self.dataset_save_path}")
            self.save(self.dataset_save_path)

        return TaskCompletion(results)
