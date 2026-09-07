import warnings

from core.task.multi.MultiTask import MultiTask
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.evidence import submit_with_evidence


def _run_task(task):
    if not hasattr(task, "actor") or not task.actor:
        raise Exception("The actor is not available")
    if hasattr(task.actor, "llm") and task.actor.llm:
        task.actor.llm.reinit_client()

    return task.run()


class ParallelTask(MultiTask):
    """ Task For Text-to-SQL """

    NAME = "ParallelTask"

    def __init__(
            self,
            **kwargs
    ):
        super().__init__(**kwargs)

    def run(self):
        if not self.tasks:
            warnings.warn(f"The `tasks` list is empty. Run is stopped. ", category=UserWarning)
            return
        # Thread pool: LLM-backed tasks are not picklable for ProcessPoolExecutor.
        max_workers = min(len(self.tasks), 8)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [submit_with_evidence(executor, _run_task, task) for task in self.tasks]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    warnings.warn(f"Task failed with error: {e}", category=UserWarning)
