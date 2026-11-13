"""The songs page's shelf: the world setting's songs by singer and its releases by date."""

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
