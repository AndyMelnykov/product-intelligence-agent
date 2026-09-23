import credentials
import set_credentials


def test_main_sets_every_required_key(monkeypatch):
    calls = []
    monkeypatch.setattr(set_credentials.keyring, "set_password", lambda service, key, value: calls.append((service, key, value)))
    monkeypatch.setattr(set_credentials.getpass, "getpass", lambda prompt: "typed-secret")

    set_credentials.main()

    assert [key for _, key, _ in calls] == credentials.REQUIRED_KEYS
    assert all(service == credentials.SERVICE for service, _, _ in calls)
    assert all(value == "typed-secret" for _, _, value in calls)
