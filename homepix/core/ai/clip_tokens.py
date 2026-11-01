"""The search model's word splitter: a phrase becomes the token numbers MobileCLIP reads.

CLIP's tokenizer, read from the model's own clip_tokenizer.json: tidy the text, split it into
words, numbers and punctuation, then join each word's letters with the file's merge list (BPE).
Small enough to keep here rather than add a library and the dozen packages it brings."""

import json
import unicodedata
from functools import lru_cache

CONTRACTIONS = ("'s", "'t", "'re", "'ve", "'m", "'ll", "'d")


def _byte_chars() -> dict[int, str]:
    """GPT-2's byte -> printable character table: every byte gets a character the vocab knows."""
    keep = [*range(ord("!"), ord("~") + 1), *range(ord("¡"), ord("¬") + 1), *range(ord("®"), ord("ÿ") + 1)]
    table, extra = {}, 0
    for b in range(256):
        if b in keep:
            table[b] = chr(b)
        else:
            table[b] = chr(256 + extra)
            extra += 1
    return table


BYTE_CHARS = _byte_chars()


def _kind(ch: str) -> str:
    if ch.isspace():
        return " "
    return {"L": "L", "N": "N"}.get(unicodedata.category(ch)[0], "P")


def words(text: str) -> list[str]:
    """Contractions, runs of letters, single digits, and runs of anything else; spaces dropped."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = next((c for c in CONTRACTIONS if text.startswith(c, i)), None)
        if c:
            out.append(c)
            i += len(c)
            continue
        kind = _kind(text[i])
        if kind == " ":
            i += 1
            continue
        if kind == "N":
            out.append(text[i])
            i += 1
            continue
        j = i + 1
        while j < n and _kind(text[j]) == kind:
            j += 1
        out.append(text[i:j])
        i = j
    return out


class ClipTokenizer:
    START, END = "<|startoftext|>", "<|endoftext|>"

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            model = json.load(f)["model"]
        self.vocab = model["vocab"]
        self.ranks = {tuple(m.split(" ") if isinstance(m, str) else m): r for r, m in enumerate(model["merges"])}
        self.unknown = self.vocab[model["unk_token"]]
        self._bpe = lru_cache(maxsize=10_000)(self._merge)

    def encode(self, text: str) -> list[int]:
        text = " ".join(unicodedata.normalize("NFC", text).split()).lower()
        ids = [self.vocab[self.START]]
        for word in words(text):
            for token in self._bpe("".join(BYTE_CHARS[b] for b in word.encode("utf-8"))):
                ids.append(self.vocab.get(token, self.unknown))
        ids.append(self.vocab[self.END])
        return ids

    def _merge(self, word: str) -> tuple[str, ...]:
        """Join neighbouring pieces, best-ranked pair first, until no known pair is left."""
        parts = [*word[:-1], word[-1] + "</w>"]
        while len(parts) > 1:
            pairs = {(a, b) for a, b in zip(parts, parts[1:], strict=False)}
            best = min(pairs, key=lambda p: self.ranks.get(p, float("inf")))
            if best not in self.ranks:
                break
            joined, k = [], 0
            while k < len(parts):
                if k < len(parts) - 1 and (parts[k], parts[k + 1]) == best:
                    joined.append(parts[k] + parts[k + 1])
                    k += 2
                else:
                    joined.append(parts[k])
                    k += 1
            parts = joined
        return tuple(parts)
