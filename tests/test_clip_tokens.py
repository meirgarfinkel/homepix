"""The search model's word splitter (in place of the tokenizers library)."""

import json

import pytest

from homepix.core.ai.clip_tokens import ClipTokenizer, words
from homepix.core.ai.models import model_path


def test_words_are_split_like_clip():
    assert words("grandma's 80th birthday!!") == ["grandma", "'s", "8", "0", "th", "birthday", "!!"]
    assert words("don't  stop...now") == ["don", "'t", "stop", "...", "now"]
    assert words("日本の桜 ½") == ["日本の桜", "½"]


def test_letters_are_joined_by_the_merge_list(tmp_path):
    vocab = {"<|startoftext|>": 0, "<|endoftext|>": 1, "c": 2, "a": 3, "t</w>": 4, "ca": 5, "cat</w>": 6, "a</w>": 7}
    f = tmp_path / "tok.json"
    f.write_text(json.dumps({"model": {"vocab": vocab, "merges": ["c a", "ca t</w>"], "unk_token": "<|endoftext|>"}}))
    tok = ClipTokenizer(f)
    assert tok.encode("CAT") == [0, 6, 1]
    assert tok.encode(" a  cat ") == [0, 7, 6, 1]
    assert tok.encode("ta") == [0, 1, 7, 1]  # "t" alone isn't known: the unknown token


# what the tokenizers library gave for the real model
KNOWN = [
    ("dog in the snow", [49406, 1929, 530, 518, 2583, 49407]),
    ("Grandma's 80th birthday!!", [49406, 10525, 568, 279, 271, 640, 1166, 748, 49407]),
    ("café  naïve façade", [49406, 15304, 1097, 35689, 563, 778, 10067, 1928, 49407]),
    ("日本の桜", [49406, 39121, 44353, 21575, 162, 94, 506, 49407]),
    ("emoji 🐶🎉", [49406, 16327, 14466, 3986, 49407]),
    ("½ ² don't", [49406, 33613, 41175, 847, 713, 49407]),
]


@pytest.mark.skipif(not model_path("clip_tokenizer.json").exists(), reason="the search model isn't downloaded")
def test_the_real_model_matches_the_tokenizers_library():
    tok = ClipTokenizer(model_path("clip_tokenizer.json"))
    for text, ids in KNOWN:
        assert tok.encode(text) == ids, text
