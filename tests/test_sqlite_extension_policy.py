import sqlite3

import pytest
from test_recovery import bundle as bundle

from jarvis_core.recovery_sqlite import deny_extension_loading


class WithoutExtensionAPI(sqlite3.Connection):
    """Real SQLite engine; only CPython's optional method is hidden."""

    def __getattribute__(self, name):
        if name == "enable_load_extension":
            raise AttributeError(name)
        return super().__getattribute__(name)


@pytest.mark.parametrize("hide_api", [False, True])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT load_extension(?)",
        "SELECT LOAD_EXTENSION(?, 'fixture_entrypoint')",
        'SELECT "load_extension"(?)',
    ],
)
def test_real_sql_loader_denied_at_prepare_with_and_without_python_api(tmp_path, hide_api, sql):
    factory = WithoutExtensionAPI if hide_api else sqlite3.Connection
    c = sqlite3.connect(":memory:", factory=factory)
    calls = []
    try:
        native_toggle = getattr(sqlite3.Connection, "enable_load_extension", None)
        assert hasattr(c, "enable_load_extension") is (not hide_api and callable(native_toggle))
        if callable(native_toggle):
            # Deliberately enable the real loader through the base class in this
            # owned fixture; hiding the Python method must not weaken SQL policy.
            native_toggle(c, True)
        functions = {row[0] for row in c.execute("PRAGMA function_list")}
        if "load_extension" not in functions:
            # Some builds omit the native loader as well as the Python toggle.
            # A real SQL function with a synthetic effect still proves prepare
            # denies its name before executing anything on those builds.
            c.create_function("load_extension", -1, lambda *_: calls.append("executed"))
        deny_extension_loading(c)
        with pytest.raises(
            sqlite3.DatabaseError, match=r"not authorized to use function:.*[Ll][Oo][Aa][Dd]_"
        ):
            c.execute(sql, (str(tmp_path / "nonexistent_extension.fixture"),)).fetchall()
        assert calls == []
        assert c.execute("SELECT 2 + 2").fetchone() == (4,)
    finally:
        c.close()


def test_installing_authorizer_invalidates_previously_cached_statement():
    c = sqlite3.connect(":memory:", factory=WithoutExtensionAPI)
    calls = []
    c.create_function("load_extension", 1, lambda value: calls.append(value) or 1)
    sql = "SELECT load_extension(?)"
    try:
        assert c.execute(sql, ("synthetic-before-policy",)).fetchone() == (1,)
        deny_extension_loading(c)
        with pytest.raises(
            sqlite3.DatabaseError, match="not authorized to use function: load_extension"
        ):
            c.execute(sql, ("must-not-run",)).fetchone()
        assert calls == ["synthetic-before-policy"]
    finally:
        c.close()


def test_failures_to_apply_policy_are_not_swallowed():
    class FailsToggle(sqlite3.Connection):
        def enable_load_extension(self, value):
            raise RuntimeError("synthetic_toggle_failure")

    class FailsAuthorizer(WithoutExtensionAPI):
        def set_authorizer(self, callback):
            raise RuntimeError("synthetic_authorizer_failure")

    for factory, code in [
        (FailsToggle, "synthetic_toggle_failure"),
        (FailsAuthorizer, "synthetic_authorizer_failure"),
    ]:
        c = sqlite3.connect(":memory:", factory=factory)
        try:
            with pytest.raises(RuntimeError, match=code):
                deny_extension_loading(c)
        finally:
            c.close()


def test_authorizer_survives_deserialize_and_private_vacuum(bundle):
    c = sqlite3.connect(":memory:", factory=WithoutExtensionAPI)
    calls = []
    c.create_function("load_extension", -1, lambda *_: calls.append("must-not-run"))
    try:
        deny_extension_loading(c)
        c.deserialize(bundle.sqlite[0].connection.serialize())
        c.execute("PRAGMA trusted_schema=OFF")
        c.execute("VACUUM")
        with pytest.raises(
            sqlite3.DatabaseError, match="not authorized to use function: load_extension"
        ):
            c.execute("SELECT load_extension('synthetic')").fetchone()
        assert calls == []
    finally:
        c.close()
