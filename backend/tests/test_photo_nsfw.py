"""The explicit photos (GLOSSARY.md「成人内容」): where the intimate tier lifts the line, she
may write what the all-ages reading strips (nudity, underwear), but never her looks, an art
style, or anyone else in the picture; the image service is given an explicit rating and a
negative prompt without the all-ages block."""

from eidolon.characters import load_characters
from eidolon.photo_prompt import NSFW_NEGATIVE_PROMPT, NSFW_RATING, image_prompt, read_tag, read_tag_adult
from eidolon.photos import CAN_SEND, CAN_SEND_INTIMATE, Eligibility, photo_turn
from tests.conftest import CharacterConfig


def test_the_adult_reading_keeps_what_the_tier_lifts_but_not_her_looks():
    adult = read_tag_adult("shot: selfie\nframing: full body\noutfit: nude, brown hair\naction: smile")
    all_ages = read_tag("shot: selfie\nframing: full body\noutfit: nude, brown hair\naction: smile")

    assert "nude" in adult.outfit  # nudity is kept
    assert "nude" not in all_ages.outfit  # and stripped all-ages
    assert "brown hair" not in adult.outfit  # her looks are never hers


def test_the_intimate_tier_asks_for_an_explicit_photo():
    intimate = photo_turn(True, "intimate", Eligibility(), adult=True)
    plain = photo_turn(True, "intimate", Eligibility())

    assert intimate is not None and intimate.prompt == CAN_SEND_INTIMATE and intimate.adult
    assert plain is not None and plain.prompt == CAN_SEND and not plain.adult


def test_the_image_service_is_sent_an_explicit_prompt(characters: CharacterConfig):
    character = load_characters(characters.path)["mano"]
    tag = read_tag_adult("shot: selfie\nframing: upper body\noutfit: nude\naction: smile\nplace: bedroom")
    prompt = image_prompt(character, tag, adult=True)

    assert NSFW_RATING in prompt.positive
    assert "rating:general" not in prompt.positive
    assert "nude" in prompt.positive
    assert prompt.negative == NSFW_NEGATIVE_PROMPT
