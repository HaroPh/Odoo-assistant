def test_config_reads_env_vars(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://x:8069")
    monkeypatch.setenv("ODOO_DB", "db1")
    monkeypatch.setenv("ODOO_USERNAME", "u1")
    monkeypatch.setenv("ODOO_PASSWORD", "p1")
    monkeypatch.delenv("MCP_RATE_LIMIT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib
    import config
    importlib.reload(config)
    assert config.ODOO_URL == "http://x:8069"
    assert config.ODOO_DB == "db1"
    assert config.ODOO_USER == "u1"
    assert config.ODOO_PWD == "p1"
    assert config.RATE_LIMIT == 60
    assert config.DATABASE_URL is None


def test_config_rate_limit_and_database_url_from_env(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://x:8069")
    monkeypatch.setenv("ODOO_DB", "db1")
    monkeypatch.setenv("ODOO_USERNAME", "u1")
    monkeypatch.setenv("ODOO_PASSWORD", "p1")
    monkeypatch.setenv("MCP_RATE_LIMIT", "10")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    import importlib
    import config
    importlib.reload(config)
    assert config.RATE_LIMIT == 10
    assert config.DATABASE_URL == "postgresql://x/y"
