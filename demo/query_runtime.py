"""Interactive Text-to-SQL runtime used by the Demo App API.

This is the engine previously living next to the Gradio UI. The product
surface is demo-app + api_server; this module only builds SQL and lists
databases.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from loguru import logger

_current_file = Path(__file__).resolve()
_project_root = _current_file.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.base import Router
from core.data_manage import DataLoader
from core.engine import Engine
from core.utils import load_dataset, save_dataset
from demo.file_to_db import load_upload_manifest
from reproduce.lib.env_config import load_dotenv, resolve_config_api_keys

import core.actor.agent  # noqa: F401  # register actors

ACTOR_BY_TYPE = {
    "parser": [
        "LinkAlignParser",
        "CHESSSelectorParser",
        "RSLSQLBiDirParser",
        "MACSQLCoTParser",
        "DINSQLCoTParser",
        "OpenSearchCoTParser",
    ],
    "generator": [
        "LinkAlignGenerator",
        "DINSQLGenerator",
        "DAILSQLGenerator",
        "CHESSGenerator",
        "MACSQLGenerator",
        "RSLSQLGenerator",
        "ReFoRCEGenerator",
        "OpenSearchSQLGenerator",
        "RecursiveGenerator",
    ],
    "optimizer": [
        "LinkAlignOptimizer",
        "RSLSQLOptimizer",
        "CHESSOptimizer",
        "AdaptiveOptimizer",
        "OpenSearchSQLOptimizer",
        "MACSQLOptimizer",
        "DINSQLOptimizer",
    ],
    "decomposer": [
        "DINSQLDecomposer",
        "MACSQLDecomposer",
        "RecursiveDecomposer",
    ],
    "scaler": [
        "ChessScaler",
        "DINSQLScaler",
        "MACSQLScaler",
        "RSLSQLScaler",
        "OpenSearchSQLScaler",
    ],
    "selector": [
        "FastExecSelector",
        "ChaseSelector",
        "CHESSSelector",
        "AgentDebateSelector",
        "OpenSearchSQLSelector",
    ],
}

WORKFLOW_SKELETONS = [
    ["generator"],
    ["parser", "generator"],
    ["parser", "generator", "optimizer"],
    ["parser", "generator", "scaler", "selector"],
    ["parser", "generator", "optimizer", "scaler", "selector"],
    ["decomposer", "parser", "generator"],
    ["decomposer", "parser", "generator", "optimizer"],
    ["decomposer", "parser", "generator", "scaler", "selector"],
    ["decomposer", "parser", "generator", "optimizer", "scaler", "selector"],
]


def load_demo_config() -> dict:
    config_path = _project_root / "demo" / "demo_config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_uploaded_db_root() -> Path:
    cfg = load_demo_config()
    p = cfg.get("paths", {}).get("uploaded_db_root")
    if p:
        return _project_root / p
    from demo.workspace import uploaded_db_dir
    return uploaded_db_dir()


def get_temp_data_dir() -> Path:
    cfg = load_demo_config()
    p = cfg.get("paths", {}).get("temp_data_dir")
    if p:
        return _project_root / p
    from demo.workspace import temp_data_dir
    return temp_data_dir()


def get_router_config_path() -> str:
    cfg = load_demo_config()
    return cfg.get("router_config", "startup_run/startup_config.json")


class SqurveDemo:
    def __init__(
            self,
            config_path: Optional[str] = None,
            provider: Optional[str] = None,
            model_name: Optional[str] = None,
            api_key: Optional[str] = None,
            base_url: Optional[str] = None,
    ):
        config_path = config_path or get_router_config_path()
        if not Path(config_path).is_absolute():
            config_path = str(_project_root / config_path)
        config_file = Path(config_path).resolve()
        config = json.loads(config_file.read_text(encoding="utf-8"))
        if provider:
            config.setdefault("api_key", {}).setdefault(provider, "your_api_key_here")
            config.setdefault("llm", {})["use"] = provider
        if model_name:
            config.setdefault("llm", {})["model_name"] = model_name
        if base_url:
            config.setdefault("llm", {})["base_url"] = base_url
        if provider and api_key:
            config.setdefault("api_key", {})[provider] = api_key
        else:
            load_dotenv(_project_root / ".env")
            config = resolve_config_api_keys(config)
        self._resolve_config_paths(config, config_file.parent)

        sys_config_path = _project_root / "config" / "sys_config.json"
        original_sys_config = Router._sys_config_path
        Router._sys_config_path = str(sys_config_path)
        try:
            self.router = Router()
        finally:
            Router._sys_config_path = original_sys_config
        self._anchor_benchmark_roots(self.router, sys_config_path.parent)
        self.router.init_config(config)
        self.engine = Engine(self.router)
        logger.info(f"SqurveDemo initialized: {self.router.use}/{self.router.model_name}")

    @staticmethod
    def _anchor_benchmark_roots(router: Router, base_dir: Path) -> None:
        """Resolve benchmark roots against the system config, not the process cwd.

        The system config writes roots like `../benchmarks/spider`, relative to
        its own folder; the Router reads them relative to the working directory,
        which breaks whenever the Demo API is started from the repository root.
        """
        for meta in router.benchmark or ():
            root_path = meta.get("root_path")
            if isinstance(root_path, str) and root_path and not Path(root_path).is_absolute():
                meta["root_path"] = str((base_dir / root_path).resolve())

    @staticmethod
    def _resolve_config_paths(config: dict, base_dir: Path) -> None:
        path_fields = {
            "dataset": ("data_source_dir",),
            "database": ("schema_source_dir", "vector_store"),
        }
        for section, fields in path_fields.items():
            values = config.get(section, {})
            for field in fields:
                value = values.get(field)
                if isinstance(value, str) and value and not Path(value).is_absolute():
                    values[field] = str((base_dir / value).resolve())

        for task in config.get("task", {}).get("task_meta", []):
            value = task.get("dataset_save_path")
            if isinstance(value, str) and value and not Path(value).is_absolute():
                task["dataset_save_path"] = str((base_dir / value).resolve())

    def generate_sql(
        self,
        question: str,
        db_id: str,
        schema_path: Optional[str] = None,
        db_path: Optional[str] = None,
        use_workflow: bool = False,
        workflow_actor_lis: Optional[List] = None,
        generate_type: str = "DINSQLGenerator",
    ) -> Dict:
        if not question or not question.strip():
            return {"sql": "", "status": "error", "message": "Please provide a question"}
        if not db_id or not db_id.strip():
            return {"sql": "", "status": "error", "message": "Please select a database"}

        try:
            started_at = time.monotonic()
            instance_id = str(uuid.uuid4())[:8]
            db_size = _compute_db_size_from_schema_path(schema_path or "", db_id.strip()) if schema_path else 0
            data_item = {
                "question": question.strip(),
                "db_id": db_id.strip(),
                "instance_id": instance_id,
                "db_type": "sqlite",
                "db_size": db_size,
            }

            temp_dir = get_temp_data_dir()
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_file = temp_dir / f"demo_{instance_id}.json"
            save_dataset(dataset=[data_item], new_data_source=temp_file)

            dataloader = DataLoader(self.router)
            dataloader.update_data_source(str(temp_file), "demo")

            schema_source_index = f"demo_{db_id}"
            schema_dir = Path(schema_path) if schema_path else None
            if schema_dir and schema_dir.exists():
                schema_file = schema_dir / "schema.json" if schema_dir.is_dir() else schema_dir
                if schema_file.exists():
                    dataloader.update_schema_save_source(
                        {schema_source_index: str(schema_file)},
                        multi_database=False,
                        vector_store=None,
                    )
                else:
                    dataloader.update_schema_save_source(
                        {schema_source_index: str(schema_dir)},
                        multi_database=False,
                        vector_store=None,
                    )
            else:
                return {"sql": "", "status": "error", "message": "Schema path not found"}

            if db_path:
                dataloader.set_db_path("demo", db_path)

            dataset = dataloader.generate_dataset(
                "demo",
                schema_source_index,
                is_schema_final=True,
            )
            if dataset is None:
                return {"sql": "", "status": "error", "message": "Failed to create dataset"}

            if db_path:
                dataset.db_path = db_path

            llm = self.engine.dataloader.llm

            if use_workflow and workflow_actor_lis:
                from core.actor.agent.WorkflowAgent import WorkflowAgent

                agent = WorkflowAgent(
                    dataset=dataset,
                    llm=llm,
                    actor_lis=workflow_actor_lis,
                    actor_args={},
                )
                result = agent.act(0)
            else:
                from core.task.meta.GenerateTask import GenerateTask

                task = GenerateTask(
                    llm=llm,
                    generate_type=generate_type,
                    dataset=dataset,
                    task_id=f"demo_{instance_id}",
                    eval_type=[],
                    open_parallel=False,
                    max_workers=1,
                    is_save_dataset=False,
                )
                actor = task.load_actor()
                if actor is None:
                    return {"sql": "", "status": "error", "message": f"Generator {generate_type} not found"}
                result = actor.act(0)

            sql = ""
            if isinstance(result, str):
                sql = result
            elif isinstance(result, dict):
                sql = result.get("pred_sql", result.get("sql", str(result)))
            else:
                sql = str(result)

            if sql and (sql.endswith(".sql") or "/" in sql.replace("\\", "/")):
                sql_path = Path(sql)
                if not sql_path.is_absolute():
                    sql_path = _project_root / sql_path
                if sql_path.exists() and sql_path.is_file():
                    try:
                        sql = sql_path.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass

            trace = dataset[0].get("_actor_trace", []) if len(dataset) else []
            if not trace:
                trace = [{
                    "actor_name": workflow_actor_lis[-1] if use_workflow and workflow_actor_lis else generate_type,
                    "stage_name": "interactive_query",
                    "elapsed_s": round(time.monotonic() - started_at, 3),
                    "error": None,
                }]
            return {
                "sql": sql,
                "status": "success",
                "message": "SQL generated",
                "instance_id": instance_id,
                "trace": trace,
            }

        except Exception as e:
            logger.exception("Error generating SQL")
            return {"sql": "", "status": "error", "message": str(e)}


def _compute_db_size_from_schema_path(schema_path: str, db_id: Optional[str] = None) -> int:
    path = Path(schema_path)
    schema_file = path / "schema.json" if path.is_dir() else path
    if not schema_file.exists():
        return 0
    try:
        data = load_dataset(schema_file)
        schemas = data if isinstance(data, list) else [data]
        for s in schemas:
            if not isinstance(s, dict):
                continue
            if db_id and s.get("db_id") != db_id:
                continue
            col_names = s.get("column_names") or s.get("column_names_original") or []
            if not col_names:
                return 0
            if len(col_names) > 1 and col_names[0][1] == "*":
                return len(col_names) - 1
            return len(col_names)
    except Exception:
        pass
    return 0


BUILTIN_BENCHMARK_DATABASES = (
    ("spider", "benchmarks/spider/database", "benchmarks/spider/dev/schema.json"),
    ("bird", "benchmarks/bird/dev/database", "benchmarks/bird/dev/schema.json"),
    ("ambidb", "benchmarks/ambidb/database", "benchmarks/ambidb/schema.json"),
    ("BookSQL", "benchmarks/BookSQL/database", "benchmarks/BookSQL/val/schema.json"),
    ("bull-cn", "benchmarks/bull-cn/database", "benchmarks/bull-cn/dev/schema.json"),
    ("bull-en", "benchmarks/bull-en/database", "benchmarks/bull-en/dev/schema.json"),
    ("ehrsql-2024", "benchmarks/ehrsql-2024/database", "benchmarks/ehrsql-2024/valid/schema.json"),
    ("spider2", "benchmarks/spider2/lite/database", "benchmarks/spider2/lite/schema.json"),
)


def _builtin_database_references() -> List[Tuple[str, str, str, str]]:
    """Return installed read-only benchmark databases without question data."""
    references = []
    seen_ids = set()
    for benchmark, database_relative, schema_relative in BUILTIN_BENCHMARK_DATABASES:
        database_root = _project_root / database_relative
        schema_path = _project_root / schema_relative
        if not database_root.is_dir() or not schema_path.is_file():
            continue
        for database_path in sorted(database_root.rglob("*.sqlite")):
            db_id = database_path.stem
            if db_id in seen_ids:
                db_id = f"{benchmark}__{db_id}"
            seen_ids.add(db_id)
            references.append((db_id, benchmark, str(database_path), str(schema_path)))
    return references


def get_available_databases() -> List[Tuple[str, str, str]]:
    """Return uploaded databases plus available read-only benchmark references."""
    base_root = get_uploaded_db_root()
    manifest = load_upload_manifest(base_root)
    out = []
    seen = set()
    for e in manifest:
        db_path = e.get("db_path", "")
        if not Path(db_path).exists():
            continue
        schema_path = e.get("schema_path") or (Path(e.get("schema_base_dir", "")) / "schema.json")
        out.append((e["db_id"], db_path, str(schema_path)))
        seen.add(e["db_id"])

    for db_id, _benchmark, db_path, schema_path in _builtin_database_references():
        if db_id not in seen:
            out.append((db_id, db_path, schema_path))
    return out


def database_benchmark(db_id: str) -> Optional[str]:
    """Return the benchmark label for a built-in database, if any."""
    return next((benchmark for reference_id, benchmark, _db_path, _schema_path in _builtin_database_references() if reference_id == db_id), None)


def database_schema_id(db_path: str) -> str:
    """Return the source benchmark identifier used inside a schema JSON file."""
    return Path(db_path).stem
