"""
用法: mltgen [options] lab_file
Options:
    -b              まばたきの有効化
    # -e extension    口パク画像群の拡張子（既定値はpng）
    -f fps          フレーム毎秒
    -h              ヘルプ
    -l              音素の一覧
    -m filepath     口パク画像群のファイルパスの共通部分

    口パク画像群はファイル名の末尾が -a.png, -x.png などで終わる複数の画像ファイル。
    -m は口パク画像群のファイルパスの共通部分、すなわち -a.png などのハイフンより前の部分をを指定します。
    ハイフンの直後の英数字がLABファイルの音素に対応します。
    たとえば、LABファイルの e の音と対応させるには口パク画像のファイル名末尾を -e.png とします。
    例外として -x.png の末尾を持つ画像はデフォルトの画像を表し、音素と画像との対応が存在しない場合に使われます。
"""

from dataclasses import dataclass
import os
import argparse
import math
import xml.etree.ElementTree as ET
import sys
import random

PHONEME_DEFAULT = "x"
BLINK_PERIOD_SEC = 0.06
BLINK_INTERVAL_MIN_SEC = 0.2
BLINK_INTERVAL_MAX_SEC = 6

type ImagePhonemeMap = dict[str, str]


@dataclass
class Options:
    blink: bool
    fps: int
    lab: str
    map: list[str]


@dataclass
class Timing:
    start: int  # 1e-7 秒単位
    end: int  # 1e-7秒単位
    phoneme: str


def get_mappings(base_path: str) -> ImagePhonemeMap:
    ip_map: ImagePhonemeMap = {}
    dirname = os.path.dirname(base_path)
    basename = os.path.basename(base_path)
    files = filter(lambda x: x.startswith(basename), os.listdir(dirname))
    for f in files:
        b, _ = os.path.splitext(f)
        if "-" not in b:
            continue
        _, phoneme = b.rsplit("-", 2)
        ip_map[phoneme] = os.path.join(dirname, f)

    return ip_map


def parse_options() -> Options:
    parser = argparse.ArgumentParser(
        usage="%(prog)s -f fps -m pathspec lab_file",
        description="発生タイミングファイルから口パク用MLTファイルを生成します",
        epilog="実行例: %(prog)s -f 30 -m ./img/chara1- file.lab",
    )
    parser.add_argument(
        "-f", "--fps", type=int, required=True, help="映像フレームレート・整数"
    )
    parser.add_argument("-m", "--map", help="口パク画像群のパス指定", required=True)
    parser.add_argument("-b", "--blink", action="store_true", help="瞬きの有効化")
    parser.add_argument("lab", help="LABファイル")
    args = parser.parse_args()
    return Options(args.blink, args.fps, args.lab, args.map)


def parse_labfile(filename: str) -> list[Timing]:
    timings: list[Timing] = []
    with open(filename, encoding="utf-8") as f:
        # Remove BOM marker if present
        if f.read(1) != "\ufeff":
            f.seek(0, 0)

        for line in f:
            start, end, phoneme = line.split()
            start, end = int(start), int(end)
            timings.append(Timing(start, end, phoneme))

    return timings


# def limited_expovariate():
# pass


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


def get_blink_timings(last_time: int) -> list[Timing]:
    timings: list[Timing] = []
    start, end = 0, 0
    while True:
        rand = limited_gauss(6, 1.2, 7, 0.2)
        start += math.floor(rand * 1e7)  # 1e7秒
        end = start + math.floor(BLINK_PERIOD_SEC * 1e7)  # 1e7秒
        timings.append(Timing(start, end, "+b"))
        if end >= last_time:
            break

    return timings


def replace_timings(timings: list[Timing], index: int, elements: list[Timing]):
    timings[index : index + 1] = elements


def merge_blink_timings(animes: list[Timing], blinks: list[Timing]) -> list[Timing]:
    i = 0
    for blink in blinks:
        while animes[i].end < blink.start:
            i += 1
            if i >= len(animes):
                break

        if i >= len(animes):
            break

        if animes[i].start <= blink.start <= animes[i].end:
            # "# [As <= Bs] < Ae"
            before = Timing(animes[i].start, blink.start, animes[i].phoneme)
            if blink.end <= animes[i].end:
                # "# As <= [Bs < [Be] <= Ae]"
                after1 = Timing(blink.start, blink.end, animes[i].phoneme + "b")
                after2 = Timing(blink.end, animes[i].end, animes[i].phoneme)
                replace_timings(animes, i, [before, after1, after2])
                i += 2

            else:
                # "# As <= [Bs < Ae] < Be"
                after = Timing(blink.start, animes[i].end, animes[i].phoneme + "b")
                replace_timings(animes, i, [before, after])
                i += 2

        if i >= len(animes):
            break

        if animes[i].start <= blink.end < animes[i].end:
            # "# [As <= [Be] < Ae]"
            before = Timing(animes[i].start, blink.end, animes[i].phoneme + "b")
            after = Timing(blink.end, animes[i].end, animes[i].phoneme)
            replace_timings(animes, i, [before, after])
            i += 1

        if i >= len(animes):
            break

        if blink.start <= animes[i].start and animes[i].end <= blink.end:
            animes[i].phoneme += "b"
            i += 1

    return animes


opt = parse_options()
fps = opt.fps
blink_enabled = opt.blink
ip_map_str = opt.map
in_path = opt.lab

ip_map = get_mappings(ip_map_str)

root = ET.Element("mlt")
ET.SubElement(root, "profile", {"frame_rate_den": "1", "frame_rate_num": f"{fps}"})

producers: dict[str, ET.Element] = {}
for phoneme in ip_map:
    entry = ET.SubElement(root, "producer", {"id": f"p_{phoneme}"})
    ET.SubElement(entry, "property", {"name": "resource"}).text = ip_map[phoneme]
    producers[phoneme] = entry

playlist = ET.SubElement(root, "playlist", {"id": "playlist0"})

anime_timings = parse_labfile(in_path)
if blink_enabled:
    blink_timings = get_blink_timings(anime_timings[-1].end)
    anime_timings = merge_blink_timings(anime_timings, blink_timings)


for line in anime_timings:
    default = PHONEME_DEFAULT
    if line.phoneme.endswith("b"):
        default += "b"

    producer = producers.get(line.phoneme, producers.get(default))
    in_time = math.floor(line.start * fps / 1e7)
    out_time = max(math.floor(line.end * fps / 1e7) - 1, in_time)

    if producer == None:
        ET.SubElement(playlist, "blank", {"length": f"{out_time - in_time + 1}"})
    else:
        ET.SubElement(
            playlist,
            "entry",
            {"producer": producer.get("id"), "in": f"{in_time}", "out": f"{out_time}"},
        )

tree = ET.ElementTree(root)
ET.indent(tree)
# tree.write(sys.stdout.fileno(), encoding="utf-8")  # write to stdout
out_file, _ = os.path.splitext(os.path.basename(in_path))
tree.write(f"{out_file}.mlt", encoding="utf-8", xml_declaration=True)
