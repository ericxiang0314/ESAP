import pytest
from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
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


def test_game_list_uses_metacritic_csv_games(client):
    response = client.get('/games')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Dota 2' in html
