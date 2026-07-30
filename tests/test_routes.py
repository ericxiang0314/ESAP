import pytest
import app as app_module
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client


def test_home_page_has_only_title_and_search(client):
    response = client.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Find your Steam Games' in html
    assert 'Search games by name' in html
    assert 'Recommended for you' not in html
    assert 'Saved profiles' not in html


def test_game_list_page_shows_full_catalog(client):
    response = client.get('/games')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Browse and sort games' in html


def test_home_page_shows_random_game_preview(client):
    response = client.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Featured games' in html


def test_profile_save_shows_recommendations(client):
    response = client.post('/profile/step/3', data={
        'profile_name': 'My profile',
        'genres': ['Action'],
        'languages': ['English'],
        'tags': ['Multiplayer'],
        'notes': 'I like fast-paced games.'
    }, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Recommended for you' in html


def test_home_page_profile_switcher_uses_saved_profile_names(client):
    client.post('/profile/step/3', data={
        'profile_name': 'Alpha',
        'genres': ['Action'],
        'languages': ['English'],
        'tags': ['Multiplayer'],
        'notes': 'Fast-paced games.'
    }, follow_redirects=True)
    client.post('/profile/step/3', data={
        'profile_name': 'Beta',
        'genres': ['Racing'],
        'languages': ['English'],
        'tags': ['Single-player'],
        'notes': 'Relaxing games.'
    }, follow_redirects=True)

    response = client.get('/?profile=Alpha')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Alpha' in html
    assert 'Beta' in html
    assert 'Recommended for you' in html


def test_game_detail_page_shows_detailed_fields(client):
    response = client.get('/game/570/dota-2')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Dota 2' in html
    assert 'Description' in html
    assert 'Metacritic score' in html
    assert 'Top critic review' in html
    assert 'Top user review' in html
    assert 'Positive keywords' in html
    assert 'Negative keywords' in html


def test_game_list_uses_metacritic_csv_games(client):
    response = client.get('/games')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Dota 2' in html


def test_load_games_returns_sample_catalog_when_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(app_module, 'MONGO_AVAILABLE', False)
    monkeypatch.setattr(app_module, 'mongo_collection', None)
    monkeypatch.setattr(app_module, 'METACRITIC_FILE', None)

    loaded = app_module.load_games(limit=5)

    assert any(game['name'] == 'Dota 2' for game in loaded)


def test_load_games_uses_full_csv_catalog_when_mongodb_is_unavailable(monkeypatch):
    monkeypatch.setattr(app_module, 'MONGO_AVAILABLE', False)
    monkeypatch.setattr(app_module, 'mongo_collection', None)

    loaded = app_module.load_games()

    assert len(loaded) >= 3000
    assert any(game['name'] == 'Dota 2' for game in loaded)


def test_get_filtered_games_deduplicates_duplicate_entries(monkeypatch):
    monkeypatch.setattr('app.games', [
        {
            'name': 'Portal 2',
            'genre': 'Action',
            'languages': ['English'],
            'tags': ['Single-player'],
            'rating': 87.0,
            'price': 19.99,
        },
        {
            'name': 'Portal 2',
            'genre': 'Action',
            'languages': ['English'],
            'tags': ['Single-player'],
            'rating': 87.0,
            'price': 19.99,
        },
        {
            'name': 'Half-Life 2',
            'genre': 'Action',
            'languages': ['English'],
            'tags': ['Single-player'],
            'rating': 95.0,
            'price': 9.99,
        },
    ])

    filtered = app_module.get_filtered_games()

    assert [game['name'] for game in filtered] == ['Half-Life 2', 'Portal 2']
