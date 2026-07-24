from app.state.preferences import PreferencesManager, UserPreferences


def test_preferences_persist_independently_and_clear(tmp_path):
    manager = PreferencesManager(tmp_path)
    selected = UserPreferences(model="glm", level="opus", mode="m")
    manager.save("user", selected)

    loaded = manager.get("user")
    assert loaded == selected
    assert loaded.complete

    cleared = manager.clear("user")
    assert cleared == UserPreferences()
    assert manager.get("user") == UserPreferences()