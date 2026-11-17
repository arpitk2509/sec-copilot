import pytest

from sec_copilot.deploy_urls import check, database_url, readonly_url

NEON = "postgresql://neondb_owner:Own3r-Pass@ep-cool-darkness-a1b2c3d4-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


def test_the_owner_url_only_changes_the_driver_part():
    assert database_url(NEON) == NEON.replace("postgresql://", "postgresql+psycopg://", 1)
    assert database_url(NEON.replace("postgresql://", "postgres://")).startswith("postgresql+psycopg://neondb_owner:")
    assert database_url(database_url(NEON)) == database_url(NEON)               # running it twice changes nothing


def test_the_readonly_url_keeps_the_host_database_and_options_but_swaps_the_login():
    url = readonly_url(NEON, "Real-Secret_123")
    assert url == ("postgresql+psycopg://copilot_readonly:Real-Secret_123@ep-cool-darkness-a1b2c3d4-pooler."
                   "ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
    assert "Own3r-Pass" not in url and "neondb_owner" not in url                  # the owner's password never leaks into it


def test_awkward_passwords_are_escaped_so_the_url_still_parses():
    url = readonly_url(NEON, "p@ss:w/rd#1")
    assert "p%40ss%3Aw%2Frd%231@" in url and url.count("@") == 1


@pytest.mark.parametrize("bad", ["", "not a url", "mysql://u:p@h/db", "postgresql://host/db", "postgresql://u:p@host", "postgresql://u:p@host/"])
def test_things_that_are_not_a_connection_string_are_rejected_with_a_helpful_message(bad):
    with pytest.raises(ValueError, match="Expected a connection string"):
        database_url(bad)
    with pytest.raises(ValueError, match="Expected a connection string"):
        readonly_url(bad, "x")


def test_an_unreachable_database_gives_a_short_readable_message_not_a_traceback():
    lines = []
    ok = check("postgresql://owner:pw@127.0.0.1:1/neondb", "pw", log=lines.append)    # nothing listens on port 1
    assert ok is False
    report = "\n".join(lines)
    assert "could not connect as the owner" in report and "Check NEON_URL" in report
    assert "Traceback" not in report and len(report) < 600