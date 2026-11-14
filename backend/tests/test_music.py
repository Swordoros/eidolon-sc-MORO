"""The songs page's shelf: the world setting's songs by singer and its releases by date."""

import shutil
from pathlib import Path

from eidolon.music import MusicLibrary
from eidolon.world import load_world
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM

WORLD = Path(__file__).resolve().parents[2] / "characters" / "world.json"


def test_the_world_setting_has_the_songs_and_albums(tmp_path: Path):
    world = load_world(WORLD)
    assert world is not None
    shelf = MusicLibrary(world, path=tmp_path / "music.db")

    assert len(shelf.songs()) == 1175
    assert len(shelf.albums()) == 168
    assert shelf.songs("櫻木真乃")


async def test_the_api_has_both_shelves(make_client: ClientFactory):
    client = make_client(FakeLLM())

    songs = (await client.get("/api/music/songs")).json()
    albums = (await client.get("/api/music/albums")).json()

    assert isinstance(songs["songs"], list)
    assert isinstance(songs["singers"], list)
    assert isinstance(albums["albums"], list)


async def test_a_user_stars_songs_for_their_own_account(
    make_client: ClientFactory, settings
):
    shutil.copyfile(WORLD, settings.characters_path.parent / "world.json")
    client = make_client(FakeLLM(), user="star-owner")
    other = make_client(FakeLLM(), user="star-other")
    song_id = (await client.get("/api/music/songs")).json()["songs"][0]["id"]

    assert (await client.get("/api/music/favorites")).json() == {"favorites": []}

    assert (await client.put(f"/api/music/favorites/{song_id}")).status_code == 204
    assert (await client.put(f"/api/music/favorites/{song_id}")).status_code == 204
    favorites = (await client.get("/api/music/favorites")).json()["favorites"]
    assert [f["song_id"] for f in favorites] == [song_id]
    assert favorites[0]["created_at"]

    assert (await other.get("/api/music/favorites")).json() == {"favorites": []}

    assert (await client.delete(f"/api/music/favorites/{song_id}")).status_code == 204
    assert (await client.get("/api/music/favorites")).json() == {"favorites": []}


async def test_starring_a_song_the_world_setting_does_not_have_is_404(make_client: ClientFactory):
    client = make_client(FakeLLM())

    response = await client.put("/api/music/favorites/not-a-song")

    assert response.status_code == 404
