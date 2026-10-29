#!/usr/bin/env python3
from dataclasses import dataclass
import math
from pathlib import Path
import argparse
import random
import xml.etree.ElementTree as ET

DEFAULT_PHONEME = "x"
PRIORITY = {
    "pau": 0,
    DEFAULT_PHONEME: 0,
    **{c: 1 for c in "kstnhfmyrwgzdbp"},
    **{c: 2 for c in "aiueo"},
}


@dataclass
class Frame:
    phoneme: str = DEFAULT_PHONEME
    blink_level: int = 0


@dataclass
class Timing:
    start: int
    end: int
    phoneme: str


@dataclass
class Image:
    """キャラクター画像の情報を保持するクラス"""

    normal: str = ""
    """通常画像のパス"""
    blink1: str = ""
    """瞬きレベル1の画像のパス"""
    blink2: str = ""
    """瞬きレベル2の画像のパス"""


@dataclass
class Event:
    start: int
    end: int
    image_path: str


def parse_lab(path: str) -> list[Timing]:
    items: list[Timing] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            s, e, p = line.split()
            items.append(Timing(int(s), int(e), p))
    return items


def parse_last(s: str) -> int:
    """
    時間長を秒数に変換する。

    - "mm:ss" 形式なら分秒として解釈
    - それ以外は秒数として解釈
    """
    s = s.strip()

    if ":" in s:
        minutes, seconds = s.split(":")
        return int(minutes) * 60 + int(seconds)

    return int(s)


def list_sounds(items: list[Timing]) -> list[str]:
    return sorted(set(x.phoneme for x in items))


def load_images(char_dir: str) -> dict[str, Image]:
    images: dict[str, Image] = {}

    for f in Path(char_dir).glob("*.png"):
        stem = f.stem
        base = stem[: stem.rfind("-")] if "-" in stem else stem
        if base not in images:
            images[base] = Image()

        if stem.endswith("-bb"):
            images[base].blink2 = str(f)

        elif stem.endswith("-b2"):
            images[base].blink2 = str(f)

        elif stem.endswith("-b"):
            images[base].blink1 = str(f)

        elif stem.endswith("-b1"):
            images[base].blink1 = str(f)

        else:
            images[base].normal = str(f)

    # 妥当性チェック
    for name, data in images.items():
        if not data.normal:
            raise RuntimeError(f"Normal image is missing: {name}")
        if data.blink2 and not data.blink1:
            raise RuntimeError(f"Blink level 2 exists without level 1: {name}")

    return images


def frame_index(time_100ns: int, fps: int) -> int:
    return round(time_100ns * fps / 1e7)


def limited_gauss(
    mu=0.0, sigma=1.0, maximum=float("nan"), minimum=float("nan"), limit_by="clip"
) -> float:
    if minimum >= maximum:
        raise ValueError("The condition `min < max` not met")

    value = random.gauss(mu, sigma)
    if limit_by == "clip":
        if math.isnan(maximum):
            value = min(value, maximum)
        if math.isnan(minimum):
            value = max(minimum, value)
    elif limit_by == "recalc":
        raise ValueError("limit_by currently only supports `clip`")

    return value


def make_blink_frames(fps: int, last_frame: int) -> list[int]:
    """瞬きを行うフレームのフレーム数リストを生成する関数"""
    timings: list[int] = []
    start = 0
    while True:
        rand = limited_gauss(6, 1.2, 7, 0.2)
        start += frame_index(math.floor(rand * 1e7), fps)  # 1e7秒
        if start >= last_frame:
            break
        if 0 < start < last_frame:
            timings.append(start)

    return timings


def compare_phoneme_priority(p1: str, p2: str) -> int:
    """音素の優先度を比較する関数。p1が優先度高なら正、p2が優先度高なら負、同じなら0を返す"""
    return PRIORITY.get(p1, 0) - PRIORITY.get(p2, 0)


def build_frames(frames: list[Frame], timings: list[Timing], fps: int):
    for t in timings:
        start_f = frame_index(t.start, fps)
        end_f = frame_index(t.end, fps)

        if end_f < start_f:
            continue

        for i in range(start_f, end_f):
            if compare_phoneme_priority(frames[i].phoneme, t.phoneme) <= 0:
                frames[i] = Frame(phoneme=t.phoneme)

    for i in make_blink_frames(fps, len(frames)):
        frames[i].blink_level = 1
        if i + 1 < len(frames):
            frames[i + 1].blink_level = 2


def build_events(frames: list[Frame], image_map: dict[str, Image]) -> list[Event]:
    events: list[Event] = []

    for i, f in enumerate(frames):
        image = image_map.get(f.phoneme, image_map.get(DEFAULT_PHONEME))
        if not image:
            raise RuntimeError(f"Image not found for phoneme: {f.phoneme}")
        if f.blink_level == 1 and image.blink1:
            path = image.blink1
        elif f.blink_level == 2 and image.blink2:
            path = image.blink2
        elif f.blink_level == 2:
            path = image.blink1
        else:
            path = image.normal

        # merge_or_append event
        if events and events[-1].image_path == path and events[-1].end == i - 1:
            events[-1].end = i
        else:
            events.append(Event(start=i, end=i, image_path=path))

    return events


def make_mlt(events: list[Event], fps: int, outfile: Path):
    root = ET.Element("mlt")
    ET.SubElement(root, "profile", {"frame_rate_num": str(fps), "frame_rate_den": "1"})

    producer_map = {}

    for ev in events:
        img = ev.image_path
        if not img or img in producer_map:
            continue

        pid = f"p{len(producer_map)}"
        producer_map[img] = pid

        p = ET.SubElement(root, "producer", {"id": pid})
        ET.SubElement(p, "property", {"name": "resource"}).text = img

    playlist = ET.SubElement(root, "playlist", {"id": "playlist0"})

    for ev in events:
        pid = producer_map.get(ev.image_path)
        if not pid:
            continue

        ET.SubElement(
            playlist,
            "entry",
            {
                "producer": pid,
                "in": str(ev.start),
                "out": str(ev.end),
            },
        )

    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(outfile, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("lab")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--last", default="0")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--list-sounds", action="store_true")
    ap.add_argument("--dir")
    args = ap.parse_args()

    timings = parse_lab(args.lab)

    if args.list_sounds:
        for p in list_sounds(timings):
            print(p)
        raise SystemExit(0)

    if args.dir is None:
        raise SystemExit("Please specify a directory with --dir")

    if args.fps <= 0:
        raise SystemExit("FPS must be a positive integer")

    if args.seed is not None:
        random.seed(args.seed)

    last_frame = int(max(parse_last(args.last), timings[-1].end / 1e7) * args.fps)

    # Load images from the specified directory
    images = load_images(args.dir)

    frames = [Frame()] * (last_frame + 1)
    build_frames(frames, timings, args.fps)

    # Build events based on the parsed timings, FPS, loaded images, and last frame
    events = build_events(frames, images)

    # Write the MLT file
    out = Path(args.lab).with_suffix(".mlt")
    make_mlt(events, args.fps, out)
    print(out)
