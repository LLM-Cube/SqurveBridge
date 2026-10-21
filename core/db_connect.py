from os import PathLike
import os
from pathlib import Path
from typing import Union, Dict
import time
import hashlib
import pandas as pd
from google.cloud import bigquery
import snowflake.connector
import sqlite3

from core.data_manage import load_dataset


def get_sqlite_result(
        sql_query: str,
        db_path: Union[str, PathLike],
        save_path: Union[str, PathLike, None] = None,
        chunk_size: int = 500,
        **kwargs
):
    """ Execute query* on an SQLite database """
    if db_path is None:
        return None, None

    db_path = Path(db_path)
    if save_path:
        save_path = Path(save_path)

    try:
        with sqlite3.connect(db_path) as conn:
            if save_path:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                for i, chunk in enumerate(pd.read_sql_query(sql_query, conn, chunksize=chunk_size)):
                    chunk.to_csv(
                        save_path,
                        mode="a" if i else "w",
                        header=not save_path.exists() or i == 0,
                        index=False,
                    )
                return True, None

            df = pd.read_sql_query(sql_query, conn)
            return df, None

    except Exception as exc:
        return None, str(exc)


def get_snowflake_sql_result(
        sql_query: str,
        db_id: str,
        credential_path: Union[str, Path, Dict],
        save_path: Union[str, PathLike, None] = None,
        timeout_seconds: int = 120,
        **kwargs
):
    """ Execute SQL query on Snowflake and return results as DataFrame or save to CSV. """
    try:
        if isinstance(credential_path, str):
            credential_path = Path(credential_path)
        elif isinstance(credential_path, dict):
            credential_path = credential_path.get("snowflake", None)

        credentials = load_dataset(credential_path)
    except Exception as e:
        return None, f"Failed to load credentials: {e}"

    try:
        with snowflake.connector.connect(database=db_id, **credentials) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout_seconds}")
                cursor.execute(sql_query)
                results = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                df = pd.DataFrame(results, columns=columns)

                if df.empty:
                    return None, "No data found for the specified query."

                if save_path:
                    save_path = Path(save_path)
                    save_path.mkdir(parents=True, exist_ok=True)
                    df.to_csv(save_path, index=False)
                return df, None

    except Exception as e:
        return None, str(e)


def get_bigquery_sql_result(
        sql_query: str,
        credential_path: Union[str, Path, Dict],
        save_path: Union[str, Path, None] = None,
        **kwargs
):
    """ Execute a SQL query in BigQuery, return result or save as CSV. """
    try:
        # Normalize credential path when provided in different formats
        if isinstance(credential_path, dict):
            credential_path = credential_path.get("big_query", None)
        if credential_path is None:
            return None, "BigQuery credential path is not provided"
        if isinstance(credential_path, (str, PathLike)):
            credential_path = Path(credential_path)

        if not credential_path.exists():
            return None, f"Credential file not found at: {credential_path}"

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credential_path)
        client = bigquery.Client()
    except Exception as e:
        return None, f"Failed to initialize BigQuery client: {e}"

    try:
        query_job = client.query(sql_query, timeout=120)
        df = query_job.result().to_dataframe()
        gb_processed = query_job.total_bytes_processed / (1024 ** 3)

        print(f"GB processed: {gb_processed:.5f} GB")

        if df.empty:
            return None, "No data found for the specified query"

        if save_path:
            save_path = Path(save_path)
            save_path.mkdir(parents=True, exist_ok=True)
            df.to_csv(save_path, index=False)
            return df, None
        else:
            if df.shape == (1, 1):
                return df.iat[0, 0], None
            else:
                return df, None

    except Exception as e:
        return None, str(e)


def get_sql_exec_result(db_type: str, **kwargs):
    if db_type == "sqlite":
        return get_sqlite_result(**kwargs)
    elif db_type == "big_query":
        return get_bigquery_sql_result(**kwargs)
    elif db_type == "snowflake":
        return get_snowflake_sql_result(**kwargs)

    return None, None


def get_sql_exec_result_with_time(db_type: str, **kwargs):
    """Execute SQL while measuring elapsed time."""
    start = time.perf_counter()
    res = get_sql_exec_result(db_type, **kwargs)
    elapsed = time.perf_counter() - start
    return elapsed, res


def execute_sql_observation(db_type: str, *, sql_query: str, db_path: str | PathLike,
                            clock=time.monotonic) -> dict:
    """Core-safe typed execution facts for offline evaluators.

    This boundary does not import evaluation code.  Unsupported backends are
    explicit; callers that need native timeout/cancellation semantics must use
    a backend adapter that can actually observe them.
    """
    if db_type != "sqlite":
        return {"status": "unsupported", "backend": db_type, "dialect": db_type,
                "backend_error_code": "unsupported_typed_execution",
                "backend_error_source": "core.db_connect"}
    started = clock()
    conn = None
    try:
        connection_started = clock()
        database_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(database_uri, uri=True)
        connection_s = clock() - connection_started
        query_started = clock()
        cursor = conn.execute(sql_query)
        query_s = clock() - query_started
        columns = [str(item[0]) for item in (cursor.description or [])]
        fetch_started = clock()
        raw_rows = cursor.fetchall()
        fetch_s = clock() - fetch_started
        materialization_started = clock()
        rows = [tuple(row) for row in raw_rows]
        materialization_s = clock() - materialization_started
        return {
            "status": "success", "backend": "sqlite", "dialect": "sqlite",
            "rows": rows, "columns": columns, "execution_complete": True,
            "fetch_complete": True, "preservation_complete": True,
            "observed_row_count": len(rows), "known_total_row_count": len(rows),
            "timing": {"query_s": query_s, "connection_s": connection_s, "fetch_s": fetch_s,
                       "materialization_s": materialization_s,
                       "total_s": clock() - started, "clock": "monotonic"},
        }
    except sqlite3.Error as exc:
        return {
            "status": "execution_error", "backend": "sqlite", "dialect": "sqlite",
            "backend_error_code": getattr(exc, "sqlite_errorname", exc.__class__.__name__),
            "backend_error_source": "sqlite", "error_type": exc.__class__.__name__,
            "error_message": str(exc),
            "timing": {"total_s": clock() - started, "clock": "monotonic",
                       "scope": "connection+query+fetch+materialization"},
        }
    finally:
        if conn is not None:
            conn.close()


def execute_sql(db_type, db_path, sql, credential):
    if db_type not in ["sqlite", "snowflake", "big_query"]:
        return "Unsupported db_type"
    args = {"sql_query": sql}
    
    # Set db_path for sqlite, db_id for other database types
    if db_type == "sqlite":
        args["db_path"] = db_path
    else:
        args["db_id"] = db_path
    
    # Add credential_path if provided (for any database type)
    if credential is not None:
        args["credential_path"] = credential
    
    evidence_run = None
    operation_id = None
    try:
        from core.evidence import current_run, identity
        evidence_run = current_run()
        operation_id = identity() if evidence_run else None
    except Exception:
        pass
    sql_ref = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if evidence_run:
        evidence_run.event(
            "db.started", operation_id=operation_id, db_type=db_type, sql_ref=sql_ref,
            sql_text=sql if evidence_run.capture_sql else None,
            sql_text_availability="present" if evidence_run.capture_sql else "disabled",
        )
    started_ns = time.monotonic_ns()
    try:
        exec_result = get_sql_exec_result(db_type, **args)
    except BaseException as exc:
        if evidence_run:
            evidence_run.event("db.finished", operation_id=operation_id, execution_status="failed",
                               error_type=type(exc).__name__, fetch_completeness="unknown",
                               preservation_completeness="unknown",
                               duration_ns=time.monotonic_ns() - started_ns)
        raise
    if isinstance(exec_result, tuple):
        if len(exec_result) == 3:
            res, err, _ = exec_result
        elif len(exec_result) == 2:
            res, err = exec_result
        else:
            res = exec_result[0]
            err = None
    else:
        res = exec_result
        err = None
    if err:
        if evidence_run:
            evidence_run.event("db.finished", operation_id=operation_id, execution_status="failed",
                               error_type="database_error", fetch_completeness="unknown",
                               preservation_completeness="unknown",
                               duration_ns=time.monotonic_ns() - started_ns)
        return err
    if isinstance(res, pd.DataFrame) and res.empty:
        if evidence_run:
            result_ref = evidence_run.artifact(res.to_dict(orient="split")) if evidence_run.capture_outputs else None
            evidence_run.event("db.finished", operation_id=operation_id, execution_status="success",
                               fetch_completeness="complete",
                               preservation_completeness="complete" if result_ref else "disabled",
                               result_ref=result_ref, observed_row_count=0, known_total_row_count=0,
                               duration_ns=time.monotonic_ns() - started_ns)
        return "No data found for the specified query"
    if res is None:
        if evidence_run:
            evidence_run.event("db.finished", operation_id=operation_id, execution_status="success",
                               fetch_completeness="unknown", preservation_completeness="unknown",
                               observed_row_count=None, known_total_row_count=None,
                               duration_ns=time.monotonic_ns() - started_ns)
        return "No data found for the specified query"
    if isinstance(res, pd.DataFrame):
        if evidence_run:
            result_ref = evidence_run.artifact(res.to_dict(orient="split")) if evidence_run.capture_outputs else None
            evidence_run.event("db.finished", operation_id=operation_id, execution_status="success",
                               fetch_completeness="complete",
                               preservation_completeness="complete" if result_ref else "disabled",
                               result_ref=result_ref, observed_row_count=len(res),
                               known_total_row_count=len(res), duration_ns=time.monotonic_ns() - started_ns)
        return str(res)
    if evidence_run:
        evidence_run.event("db.finished", operation_id=operation_id, execution_status="success",
                           fetch_completeness="unknown", preservation_completeness="unknown",
                           observed_row_count=None, duration_ns=time.monotonic_ns() - started_ns)
    return str(res)
