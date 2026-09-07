# Common execution path

Status: implemented

Actor invocation, `Engine.execute`, sequential calls and thread-pool scheduling preserve evidence context and record start, finish, parent and task relationships without changing returned values or business exceptions.

Acceptance: enabled/disabled, exception, Engine and concurrent execution tests pass.
