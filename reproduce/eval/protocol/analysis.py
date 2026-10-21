from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import sqlglot
from pydantic import Field
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .models import StrictModel


class JoinAnalysis(StrictModel):
    kind: str
    condition: str | None


class OrderAnalysis(StrictModel):
    expression: str
    direction: Literal["ASC", "DESC"]


class SQLAnalysis(StrictModel):
    analysis_protocol: str = "sqlglot-structural-analysis@2"
    analysis_id: str
    sql_identity: str
    parser: Literal["sqlglot"] = "sqlglot"
    parser_version: str
    dialect: str
    parse_success: bool
    schema_resolution: Literal["resolved", "unresolved", "ambiguous", "not_provided", "parse_failed"]
    parse_error: str | None = None
    projections: list[str] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    literals: list[dict[str, Any]] = Field(default_factory=list)
    joins: list[JoinAnalysis] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderAnalysis] = Field(default_factory=list)
    limit: int | None = None
    nested_scope_count: int = 0
    set_operations: list[str] = Field(default_factory=list)
    table_references: list[str] = Field(default_factory=list)
    column_references: list[str] = Field(default_factory=list)
    unresolved_references: list[str] = Field(default_factory=list)
    ambiguous_references: list[str] = Field(default_factory=list)


def structural_analysis(sql: str, dialect: str = "", *, schema: dict[str, set[str]] | None = None) -> SQLAnalysis:
    sql_identity = hashlib.sha256(sql.encode()).hexdigest()
    normalized_schema = None
    if schema is not None:
        normalized_schema = {table.lower(): sorted(column.lower() for column in columns)
                             for table, columns in sorted(schema.items())}
    analysis_id = hashlib.sha256(json.dumps(
        {"protocol": "sqlglot-structural-analysis@2", "sql": sql_identity,
         "dialect": dialect, "schema": normalized_schema}, sort_keys=True,
    ).encode()).hexdigest()
    identity_fields = {"analysis_id": analysis_id, "sql_identity": sql_identity,
                       "parser_version": sqlglot.__version__}
    try:
        root = sqlglot.parse_one(sql, read=dialect or None)
    except Exception as exc:
        return SQLAnalysis(**identity_fields, dialect=dialect, parse_success=False,
                           schema_resolution="parse_failed", parse_error=exc.__class__.__name__)
    if schema is not None:
        schema = {table.lower(): {column.lower() for column in columns}
                  for table, columns in schema.items()}
    select = root.find(exp.Select)
    projections = [item.sql(dialect=dialect or None) for item in (select.expressions if select else [])]
    where = root.find(exp.Where)
    predicates = [item.sql(dialect=dialect or None) for item in where.find_all(exp.Predicate)] if where else []
    literals = []
    for literal in root.find_all(exp.Literal):
        literals.append({"type": "string" if literal.is_string else "number", "value": literal.this})
    joins = []
    for join in root.find_all(exp.Join):
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        joins.append(JoinAnalysis(kind=side or kind or "INNER",
                                  condition=join.args["on"].sql(dialect=dialect or None) if join.args.get("on") else None))
    group = root.find(exp.Group)
    group_by = [item.sql(dialect=dialect or None) for item in group.expressions] if group else []
    order = root.find(exp.Order)
    order_by = []
    if order:
        for ordered in order.expressions:
            order_by.append(OrderAnalysis(expression=ordered.this.sql(dialect=dialect or None),
                                          direction="DESC" if ordered.args.get("desc") else "ASC"))
    limit_exp = root.find(exp.Limit)
    limit = None
    if limit_exp and isinstance(limit_exp.expression, exp.Literal):
        try:
            limit = int(limit_exp.expression.this)
        except ValueError:
            pass
    columns = []
    unresolved: list[str] = []
    ambiguous: list[str] = []
    scopes = list(traverse_scope(root))
    for scope in scopes:
        source_tables = {
            alias.lower(): source.name.lower()
            for alias, source in scope.sources.items()
            if isinstance(source, exp.Table)
        }
        derived_columns = {
            alias.lower(): {name.lower() for name in source.expression.named_selects}
            for alias, source in scope.sources.items()
            if isinstance(source, Scope)
        }
        for column in scope.columns:
            name = column.name.lower()
            if column.table:
                table = source_tables.get(column.table.lower())
                derived = derived_columns.get(column.table.lower())
                if table is None and derived is not None and name in derived:
                    columns.append(f"{column.table.lower()}.{name}")
                    continue
                if table is None:
                    unresolved.append(f"{column.table.lower()}.{name}")
                    continue
                column_identity = f"{table}.{name}"
                columns.append(column_identity)
                if schema is not None and (table not in schema or name not in schema[table]):
                    unresolved.append(column_identity)
            elif schema is None:
                columns.append(name)
            else:
                owners = [table for table in source_tables.values()
                          if table in schema and name in schema[table]]
                owners.extend(alias for alias, names in derived_columns.items() if name in names)
                if len(owners) == 1:
                    columns.append(f"{owners[0]}.{name}")
                elif len(owners) > 1:
                    ambiguous.append(name)
                else:
                    unresolved.append(name)
    if schema is None:
        resolution = "not_provided"
    elif ambiguous:
        resolution = "ambiguous"
    elif unresolved:
        resolution = "unresolved"
    else:
        resolution = "resolved"
    return SQLAnalysis(
        **identity_fields, dialect=dialect, parse_success=True, schema_resolution=resolution,
        projections=projections, predicates=predicates,
        literals=literals, joins=joins, group_by=group_by, order_by=order_by, limit=limit,
        nested_scope_count=max(0, len(scopes) - 1),
        set_operations=[f"{node.key.upper()} {'DISTINCT' if node.args.get('distinct') else 'ALL'}"
                        for cls in (exp.Union, exp.Intersect, exp.Except)
                        for node in root.find_all(cls)],
        table_references=sorted({table.name for table in root.find_all(exp.Table)}),
        column_references=sorted(set(columns)),
        unresolved_references=sorted(set(unresolved)), ambiguous_references=sorted(set(ambiguous)),
    )


def structural_match(left: SQLAnalysis, right: SQLAnalysis) -> bool | None:
    if not left.parse_success or not right.parse_success:
        return None
    ignored = {"analysis_id", "sql_identity", "parser_version", "parse_error", "schema_resolution",
               "unresolved_references", "ambiguous_references"}
    a = left.model_dump(exclude=ignored)
    b = right.model_dump(exclude=ignored)
    return a == b


def schema_exact_match(predicted: set[str] | None, reference: set[str] | None,
                       *, missing_reference_policy: Literal["unavailable", "empty"] = "unavailable") -> bool | None:
    if reference is None and missing_reference_policy == "unavailable":
        return None
    return (predicted or set()) == (reference or set())
