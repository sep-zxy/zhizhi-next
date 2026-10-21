from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 1024


def polygon(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], color: str) -> None:
    draw.polygon(points, fill=color)


def build_logo() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    polygon(
        draw,
        [(288, 64), (736, 64), (960, 288), (960, 736), (736, 960), (288, 960), (64, 736), (64, 288)],
        "#3157D5",
    )
    polygon(
        draw,
        [(310, 128), (714, 128), (896, 310), (896, 714), (714, 896), (310, 896), (128, 714), (128, 310)],
        "#11192A",
    )
    polygon(draw, [(214, 316), (350, 244), (494, 250), (494, 716), (370, 656), (248, 664)], "#5D7CFF")
    polygon(draw, [(810, 316), (674, 244), (530, 250), (530, 716), (654, 656), (776, 664)], "#1B8A70")
    polygon(draw, [(470, 246), (512, 332), (554, 246), (544, 716), (512, 790), (480, 716)], "#11192A")
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    logo = build_logo()
    logo.save(ASSETS / "vault-manager-logo.png", optimize=True)
    logo.save(
        ASSETS / "vault-manager.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
