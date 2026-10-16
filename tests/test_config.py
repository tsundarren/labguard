from app.config import normalize_database_url


def test_host_style_urls_are_pointed_at_psycopg3():
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_database_url("postgresql://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"


def test_urls_that_already_name_a_driver_are_unchanged():
    url = "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url(url) == url
