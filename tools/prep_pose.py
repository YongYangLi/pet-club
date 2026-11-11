# -*- coding: utf-8 -*-
"""把角色图处理成可用的桌宠姿势素材。

两种输入，自动识别：

  **带透明通道的抠图**（素材/removebg/<role>/<pose>.png）
      只做「裁 → 缩 → 贴底」，一个像素都不改。抠图是人的活，
      机器只负责把 9 张图的对齐和大小统一。

  **没抠过的原图**（素材/原图/<role>/<pose>.png，浅灰渐变影棚布）
      先切背景再裁。这条路是兜底：背景是渐变 + 接触阴影，
      自动抠图偶尔会咬掉浅色衣服的边缘，能做但不能要求完美。

素材流向：
    素材/原图/<role>/<pose>.png      出图的原样（保留，别覆盖）
       ↓  人工抠图（你自己的工具 / remove.bg / PS）
    素材/removebg/<role>/<pose>.png  透明底
       ↓  python tools/prep_pose.py --dir 素材/removebg
    assets/roles/<role>/<pose>.png   216px 高、脚贴底边、可直接用

跑法：
    python tools/prep_pose.py <源图> <role> <pose>     # 单张
    python tools/prep_pose.py --dir 素材/removebg       # 整批（推荐）
    python tools/prep_pose.py --uniform 素材/removebg p5  # 统一系数（跳起/下落不放大）
"""

import os
import sys
from collections import deque

from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_H = 216          # 和现有 idle(210) / wave(234) 一个量级
MIN_AREA = 2500         # 小于这个像素数的前景块一律当噪声（1024x1536 的 0.16%）

# 不是每个姿势都该和站姿一样高。「缩到同高」对站着、挥手、喝水是对的，
# 对蜷着睡、趴着爬就错了 —— 蜷成一团的身高本来就只有站着的六成，
# 硬拉到 210 会变成一只和站着一样大的球，趴在地上像头牛。
# 这里的数字是「占站姿高度的比例」，取自 p1~p3 三套老素材的实测值。
POSE_SCALE = {
    'sleep': 0.66,      # p1/p2/p3 分别是 0.77 / 0.55 / 0.70
    'crawl_1': 0.74,    # 0.79 / 0.73 / 0.74
    'crawl_2': 0.74,
}


def _lum(c):
    return c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114


def _spread(c):
    """三通道极差：接近 0 就是灰，够大才可能是皮肤/衣服的颜色。"""
    return max(c) - min(c)


def _bg_refs(px, w, h, margin=8):
    """每一行左右两端各自的背景色。

    AI 图的背景不是一块白，而是一张浅灰影棚布：上浅下深，左右还带一点暗角。
    所以背景色得按「行」甚至按「列」估 —— 拿一个全局色去比，深的地方填不掉，
    浅的地方又会把浅蓝 T 恤、白球鞋一起吃掉。
    """
    left, right = [], []
    for y in range(h):
        ls = sorted(px[x, y] for x in range(margin))
        rs = sorted(px[x, y] for x in range(w - margin, w))
        left.append(ls[len(ls) // 2])
        right.append(rs[len(rs) // 2])
    return left, right


def _dist(c, ref):
    return max(abs(c[0] - ref[0]), abs(c[1] - ref[1]), abs(c[2] - ref[2]))


def cut_background(im):
    """把「和画面边缘连着的背景」挖成透明。

    为什么不是「白色就删」：人物身上也有浅色（眼白、杯子、白球鞋、浅蓝 T），
    按颜色一刀切会在脸上、衣服上打洞。只有连到边缘的才算背景。

    为什么不用 Pillow 自带的 floodfill：它拿每个像素和**种子色**比，
    而 AI 图的背景是一条渐变（上 182 下 203、左右还有暗角），差一点就填不过去，
    留下一片一片的灰斑。这里改成和**这一行左右边界的背景色**比：
    背景本身是缓变的，人物边界是突变的，按「离背景色多远」走既不会卡在渐变上，
    也不会顺着浅色衣服漫进人物。
    """
    rgb = im.convert('RGB')
    w, h = rgb.size
    px = rgb.load()
    left, right = _bg_refs(px, w, h)
    tol = 24

    def ref_at(x, y):
        a, b = left[y], right[y]
        t = x / float(max(1, w - 1))
        return (a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                a[2] + (b[2] - a[2]) * t)

    bg = bytearray(w * h)
    q = deque()

    def seed(x, y):
        i = y * w + x
        if not bg[i] and _dist(px[x, y], ref_at(x, y)) <= tol:
            bg[i] = 1
            q.append((x, y))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            i = ny * w + nx
            if bg[i]:
                continue
            if _dist(px[nx, ny], ref_at(nx, ny)) <= tol:
                bg[i] = 1
                q.append((nx, ny))

    # 第二遍：地上的接触阴影暂时不处理 —— 它和「浅色衣服的阴影面」几乎同色，
    # 自动抠图分不开。宁可脚边留一点灰（看着像影子），也不要在浅蓝衬衫上开洞。
    # 真正干净的抠图交给素材/removebg 那条路。


    # 只剩大块前景：地上的碎影、水印残渣都是小块，一律丢掉
    fg = bytearray(0 if bg[i] else 1 for i in range(w * h))
    seen = bytearray(w * h)
    keep = bytearray(w * h)
    for i in range(w * h):
        if not fg[i] or seen[i]:
            continue
        comp = []
        dq = deque([i])
        seen[i] = 1
        while dq:
            j = dq.popleft()
            comp.append(j)
            x, y = j % w, j // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    k = ny * w + nx
                    if fg[k] and not seen[k]:
                        seen[k] = 1
                        dq.append(k)
        if len(comp) >= MIN_AREA:
            for j in comp:
                keep[j] = 1

    # 边缘残留：贴着透明区、颜色又等于「这一处的背景色」的像素，一层层剥掉
    for _ in range(3):
        drop = []
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                i = y * w + x
                if not keep[i]:
                    continue
                if keep[i - 1] and keep[i + 1] and keep[i - w] and keep[i + w]:
                    continue            # 内部像素不动
                if _dist(px[x, y], ref_at(x, y)) <= 16:
                    drop.append(i)
        if not drop:
            break
        for i in drop:
            keep[i] = 0

    alpha = bytes(255 if keep[i] else 0 for i in range(w * h))
    a = Image.frombytes('L', (w, h), alpha)
    # 收一圈再羽化：AI 图边缘有一圈发白的软边，直接裁会留白毛
    a = a.filter(ImageFilter.MinFilter(3))
    a = a.filter(ImageFilter.GaussianBlur(0.9))

    out = rgb.convert('RGBA')
    out.putalpha(a)
    return out


def drop_specks(im, min_px=12):
    """缩到 216 之后再把孤立碎点清掉。

    源图上那点「鞋底反光 / 影子碎片」只有 1~2 px，肉眼几乎看不见，
    但在桌面上是一成不变的：它天天挂在脚边，比什么都显眼。
    只在缩完之后做 —— 源图上做会连发丝和手指一起削掉。
    """
    a = im.split()[3]
    w, h = a.size
    px = a.load()
    solid = bytearray(1 if px[x, y] > 40 else 0
                      for y in range(h) for x in range(w))
    seen = bytearray(w * h)
    kill = []
    for i in range(w * h):
        if not solid[i] or seen[i]:
            continue
        comp = []
        dq = deque([i])
        seen[i] = 1
        while dq:
            j = dq.popleft()
            comp.append(j)
            x, y = j % w, j // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (-1, -1), (1, -1), (-1, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    k = ny * w + nx
                    if solid[k] and not seen[k]:
                        seen[k] = 1
                        dq.append(k)
        if len(comp) < min_px:
            kill.extend(comp)
    # 连着人物的一像素小尾巴（鞋底反光、影子碎屑）：3x3 里几乎没同伴就是它
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if not solid[i] or i in kill:
                continue
            around = sum(solid[(y + dy) * w + (x + dx)]
                         for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                         if 0 <= x + dx < w and 0 <= y + dy < h)
            if around <= 3:
                kill.append(i)
    # 细长的「影子小尾巴」：脚底下常挂着一条 3px 宽、几像素高的灰线，
    # 3x3 判不出来（它上下都有同伴），但 5x5 里几乎是空的。
    # 手指、发丝、杯口都被人物包着，5x5 覆盖率远高于这个阈值。
    for _ in range(3):
        thin = []
        for y in range(h):
            for x in range(w):
                i = y * w + x
                if not solid[i] or i in kill:
                    continue
                around = sum(solid[ny * w + nx]
                             for nx in range(max(0, x - 2), min(w, x + 3))
                             for ny in range(max(0, y - 2), min(h, y + 3)))
                if around <= 9:
                    thin.append(i)
        if not thin:
            break
        kill.extend(thin)
    if not kill:
        return im
    for i in kill:
        px[i % w, i // w] = 0
    out = im.copy()
    out.putalpha(a)
    return out


def is_cutout(im):
    """已经是抠好的图？—— 有 alpha 通道、而且真的有透明像素。

    原图是 RGB 或全不透明的 RGBA，抠图是带透明边的 RGBA，
    这样区分比让调用方记得加参数可靠。
    """
    if im.mode not in ('RGBA', 'LA', 'PA'):
        im = im.convert('RGBA')
    a = im.split()[3]
    lo, hi = a.getextrema()
    return lo < 200


def fit(im, target_h=TARGET_H, clean=False):
    """裁到人物 → 等比缩到统一高度（不放大）→ 贴到画布底边。

    clean=True 时额外清碎点，只在「自己抠的图」上用：人工抠图很干净，
    再去做连通域和细结构判断，反而会把手指、发梢削掉。
    """
    box = im.split()[3].getbbox()
    if box:
        im = im.crop(box)
    if im.height > target_h:
        scale = target_h / float(im.height)
        im = im.resize((max(1, int(round(im.width * scale))), target_h),
                       Image.LANCZOS)
    if clean:
        im = drop_specks(im)
        box = im.split()[3].getbbox()
        if box:
            im = im.crop(box)
    if im.height < target_h:
        canvas = Image.new('RGBA', (im.width, target_h), (0, 0, 0, 0))
        canvas.alpha_composite(im, (0, target_h - im.height))
        im = canvas
    return im


def shrink(im, k, floor):
    """按比例缩小（**不留画布**）。

    不能垫透明补回原高：宠物窗口是按图片尺寸开的，上方多留一截透明，
    整只宠物的「窗口顶端」就飘在半空 —— 气泡和右键位置全会跟着偏。
    老素材（p1~p3 的 sleep/crawl）本来也是「图片高度 = 人物高度」，
    所以这里缩完直接返回，对齐交给 Pet 的底边定位。
    """
    if not 0 < k < 1:
        return im
    box = im.split()[3].getbbox()
    if box:
        im = im.crop(box)
    h = max(1, int(round(im.height * k)))
    w = max(1, int(round(im.width * h / float(im.height))))
    return im.resize((w, h), Image.LANCZOS)


def build(src, role, pose, target_h=TARGET_H):
    """一张源图 → assets/roles/<role>/<pose>.png，返回 (宽, 高, 是否抠图输入)。"""
    raw = Image.open(src)
    precut = is_cutout(raw)
    if precut:
        im = fit(raw.convert('RGBA'), target_h)
    else:
        im = fit(cut_background(raw), target_h, clean=True)
    im = shrink(im, POSE_SCALE.get(pose, 1.0), target_h)

    return _save(im, role, pose, precut)


def _bbox_h(path):
    im = Image.open(path).convert('RGBA')
    box = im.split()[3].getbbox()
    return (box[3] - box[1]) if box else 0


def build_uniform(src_dir, role, pose, unit_h, target_h=TARGET_H, ref_h=None):
    """统一系数：所有姿势乘同一个 k（跳过 fit 的「各缩到同高」）。

    为什么要这个而不是 build()：fit() 把「这个姿势的包围盒高度」缩到 target_h。
    对站姿没问题，但 jump / fall / drag 的包围盒本来就矮（腿弯着、脚离地），
    硬拉到同高等于把它们放大 7%~13% —— 切到这几个姿势时头会突然胀一圈。
    这里改成 k = target_h / idle 的包围盒高，所有姿势共用，
    跳起时就自然矮一点，和真实镜头一致。

    例外是 POSE_SCALE 里那几个（sleep / crawl）：它们要「看着还是同一只宠物
    蜷着/趴着」，按老素材的经验比例再缩；而且 crawl 两帧必须共用一个 k，
    否则交替播放时头会一跳一跳（ref_h 就是为这个留的）。
    """
    src = os.path.join(src_dir, pose + '.png')
    raw = Image.open(src)
    precut = is_cutout(raw)
    im = raw.convert('RGBA') if precut else cut_background(raw)
    box = im.split()[3].getbbox()
    if box:
        im = im.crop(box)

    if pose in POSE_SCALE:
        k = target_h * POSE_SCALE[pose] / float(ref_h or im.height)
    else:
        k = target_h / float(unit_h)

    w = max(1, int(round(im.width * k)))
    h = max(1, int(round(im.height * k)))
    im = im.resize((w, h), Image.LANCZOS)
    return _save(im, role, pose, precut)


def _save(im, role, pose, precut):
    dst_dir = os.path.join(ROOT, 'assets', 'roles', role)
    os.makedirs(dst_dir, exist_ok=True)
    im.save(os.path.join(dst_dir, pose + '.png'))

    prev = Image.new('RGBA', (im.width + 40, im.height + 40), (58, 60, 66, 255))
    prev.alpha_composite(im, (20, 20))
    pv = os.path.join(ROOT, 'assets', '_gen', '%s_%s_preview.png' % (role, pose))
    os.makedirs(os.path.dirname(pv), exist_ok=True)   # 第一次跑时这个目录还不存在
    prev.convert('RGB').save(pv)
    return im.width, im.height, precut


def main():
    args = [a for a in sys.argv[1:] if a]
    target_h = TARGET_H
    if '--h' in args:                   # 和 idle 对齐时用：--h 210
        i = args.index('--h')
        target_h = int(args[i + 1])
        del args[i:i + 2]

    if args[:1] == ['--uniform']:
        if len(args) < 3:
            print('用法：prep_pose.py --uniform <removebg 根目录> <role>')
            return 2
        root, role = args[1], args[2]
        d = os.path.join(root, role)
        unit = _bbox_h(os.path.join(d, 'idle.png'))
        if not unit:
            print('没有 idle.png 或其包围盒为空：%s' % d)
            return 1
        # crawl 两帧共用一个 k：折射取 crawl_1 的包围盒高当分母
        ref = {}
        c1 = os.path.join(d, 'crawl_1.png')
        if os.path.exists(c1):
            ref['crawl_2'] = _bbox_h(c1)
        print('统一系数：k = %d / %d = %.5f（来源 %s）'
              % (target_h, unit, target_h / float(unit), d))
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith('.png'):
                continue
            pose = f[:-4]
            w, h, pre = build_uniform(d, role, pose, unit, target_h,
                                      ref.get(pose))
            print('  %-4s %-8s %3dx%-3d %s' % (
                role, pose, w, h, '抠图直裁' if pre else '自动抠图'))
        return 0

    if args[:1] == ['--dir']:
        root = args[1] if len(args) > 1 else os.path.join(ROOT, '素材', 'removebg')
        only = set(args[2:])            # 可以只重建某几个姿势：... --dir 目录 drink heart
        jobs = []
        for role in sorted(os.listdir(root)):
            d = os.path.join(root, role)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.lower().endswith('.png'):
                    pose = f[:-4]
                    if only and pose not in only:
                        continue
                    jobs.append((os.path.join(d, f), role, pose))
        if not jobs:
            print('没找到图：%s/<role>/<pose>.png' % root)
            return 1
        print('处理 %d 张（来源 %s，目标高 %d）' % (len(jobs), root, target_h))
        for src, role, pose in jobs:
            w, h, pre = build(src, role, pose, target_h)
            print('  %-4s %-7s %3dx%-3d %s' % (
                role, pose, w, h, '抠图直裁' if pre else '自动抠图'))
        return 0

    if len(args) != 3:
        print(__doc__)
        return 2
    src, role, pose = args
    w, h, pre = build(src, role, pose)
    print('%s → assets/roles/%s/%s.png  %dx%d  %s' % (
        os.path.basename(src), role, pose, w, h,
        '（已是抠图，只裁剪）' if pre else '（原图，自动抠背景）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
