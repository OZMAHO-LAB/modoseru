#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""もどせる v0.2 ── Claude Code が 消した・動かした・上書きした テキストの ファイルを 1 手で 戻す。

  入れる   : /usr/bin/python3 modoseru.py 入れる [作業フォルダ]
  戻す     : /usr/bin/python3 ~/.modoseru/modoseru.py 戻す <ファイル> [何個前]
  一覧     : /usr/bin/python3 ~/.modoseru/modoseru.py 一覧 [作業フォルダ]
  点検     : /usr/bin/python3 ~/.modoseru/modoseru.py 点検 [作業フォルダ]
  外す     : /usr/bin/python3 ~/.modoseru/modoseru.py 外す [作業フォルダ]
  控える   : （フックが 呼ぶ）/usr/bin/python3 ~/.modoseru/modoseru.py 控える --フック "メモ"
  自己検査 : /usr/bin/python3 modoseru.py --自己検査

約束する こと＝作業フォルダの 中の テキストの ファイル（下の 拡張子）を、Claude Code が Bash を 走らせる 前と
ファイルを 書いた 後に 自動で 控える。消えても `戻す` で 戻る。
約束しない こと＝写真・動画・大きい ファイル（5 MB 超）・作業フォルダの 外・ディスクの 故障（控えは 同じ ディスクの 上
＝Time Machine は 別に 要る）。外の ネット・外の モデルは 呼ばない。書く のは 作業フォルダの
`.claude/settings.local.json` と `~/.modoseru/` だけ（`~/.claude/` には 書かない）。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata

VERSION = "v0.2"
TEXT_EXT = [
    "md", "txt", "csv", "tsv", "json", "yaml", "yml", "toml", "ini", "cfg",
    "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "html", "htm", "css", "scss",
    "sh", "zsh", "bash", "rb", "go", "rs", "java", "kt", "swift", "c", "h", "cpp",
    "sql", "xml", "svg", "php", "vue", "svelte", "tex", "rtf", "log",
]
MAX_BYTES = 5 * 1024 * 1024
DENY = [
    "Bash(rm -rf:*)",
    "Bash(git reset --hard:*)",
    "Bash(git clean:*)",
    "Bash(git push --force:*)",
]
MARK = "modoseru"


def home():
    return os.path.expanduser("~")


def tool_dir():
    return os.path.join(home(), ".modoseru")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def project_of(arg=None):
    p = arg or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return nfc(os.path.realpath(os.path.expanduser(p)))


def find_project(start):
    """start から 上へ たどり、もどせる が 入って いる 作業フォルダを 返す（無ければ None）"""
    p = nfc(os.path.realpath(start))
    while True:
        if os.path.isdir(store_of(p)):
            return p
        if p == os.path.dirname(p):
            return None
        p = os.path.dirname(p)


NOT_IN = "❌ この フォルダ（と その 上）には もどせる が 入って いません: {}\n   作業フォルダの 中で 走らせるか、作業フォルダを 指定して ください。"


def store_of(project):
    h = hashlib.sha1(project.encode("utf-8")).hexdigest()[:10]
    name = os.path.basename(project.rstrip("/")) or "root"
    return os.path.join(tool_dir(), "控え", f"{name}-{h}.git")


def git(project, *args, check=True, inp=None):
    g = store_of(project)
    cmd = ["git", f"--git-dir={g}", f"--work-tree={project}",
           "-c", "user.name=modoseru", "-c", "user.email=modoseru@localhost",
           "-c", "core.quotepath=false", "-c", "core.precomposeunicode=true"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, input=inp, cwd=project)  # cwd＝根（下の フォルダから 呼んでも 全体を 拾う）
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} が 失敗: {r.stderr.strip()}")
    return r


def init_store(project):
    g = store_of(project)
    if not os.path.isdir(g):
        os.makedirs(os.path.dirname(g), exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", g], check=True)
        subprocess.run(["git", f"--git-dir={g}", "config", "core.bare", "false"], check=True)
    ex = os.path.join(g, "info", "exclude")
    os.makedirs(os.path.dirname(ex), exist_ok=True)
    lines = ["# もどせる が 拾う 物（テキストの 拡張子 だけ）", "*", "!*/"]
    for e in TEXT_EXT:
        lines += [f"!*.{e}", f"!*.{e.upper()}"]
    lines += ["node_modules/", ".git/", ".venv/", "venv/", "__pycache__/"]
    with open(ex, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(g, "もどせる_作業フォルダ.txt"), "w", encoding="utf-8") as f:
        f.write(project + "\n")


def snapshot(project, memo):
    """控える。変わりが 無ければ 何も しない。返り値＝控えた ファイル数（変わり 無しは 0）"""
    if not os.path.isdir(store_of(project)):
        return 0
    git(project, "add", "-A", ".")
    # 大きい ファイルは 外す
    r = git(project, "diff", "--cached", "--name-only", "-z")
    for p in [x for x in r.stdout.split("\0") if x]:
        fp = os.path.join(project, p)
        if os.path.isfile(fp) and os.path.getsize(fp) > MAX_BYTES:
            git(project, "rm", "-q", "--cached", "--", p, check=False)
    if git(project, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return 0
    n = len([x for x in git(project, "diff", "--cached", "--name-only", "-z").stdout.split("\0") if x])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    git(project, "commit", "-q", "-m", f"{stamp} {memo}")
    return n


def has_commits(project):
    return git(project, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def versions_of(project, rel):
    """rel が 在った 版の 一覧（新しい 順・中身が 同じ 版は 1 つに）"""
    if not has_commits(project):
        return []
    out = []
    seen = set()
    revs = git(project, "rev-list", "HEAD", "--", rel).stdout.split()
    for c in revs:
        for cand in (c, c + "^"):
            r = git(project, "rev-parse", "-q", "--verify", f"{cand}:{rel}", check=False)
            if r.returncode == 0:
                blob = r.stdout.strip()
                if blob not in seen:
                    seen.add(blob)
                    when = git(project, "log", "-1", "--format=%ad", "--date=format:%Y-%m-%d %H:%M:%S",
                               cand, check=False).stdout.strip()
                    out.append((cand, blob, when))
    return out


def cmd_restore(args):
    if not args:
        print("使い方: 戻す <ファイル> [何個前＝1 が いちばん 新しい 控え]")
        return 1
    project = project_of(None)
    target = nfc(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(args[0]))))
    # 作業フォルダを 上へ たどって 探す（控えが ある 所）
    p = os.path.dirname(target)
    while True:
        if os.path.isdir(store_of(p)):
            project = p
            break
        if p == os.path.dirname(p):
            print(f"❌ この ファイルの 作業フォルダに もどせる が 入って いません: {target}")
            return 1
        p = os.path.dirname(p)
    rel = os.path.relpath(target, project)
    nth = int(args[1]) if len(args) > 1 else 1
    snapshot(project, "戻す 前の 控え")
    vs = versions_of(project, rel)
    if os.path.exists(target):  # いまと 同じ 中身の 版は 数えない（1＝いまと 違う 中で いちばん 新しい 版）
        cur = subprocess.run(["git", "hash-object", target], capture_output=True, text=True).stdout.strip()
        same = [v for v in vs if v[1] == cur]
        vs = [v for v in vs if v[1] != cur]
        if not vs and same:
            print(f"✅ 控えの 版は 全部 いまの 中身と 同じです（戻す 物は ありません）: {rel}")
            return 0
    if not vs:
        print(f"❌ 控えに 見つかりません: {rel}（テキストの 拡張子・5 MB 以下・作業フォルダの 中 だけを 控えます）")
        return 1
    if nth < 1 or nth > len(vs):
        print(f"❌ いまと 違う 版は {len(vs)} 個 です（1〜{len(vs)} で 指定・1＝いちばん 新しい）")
        return 1
    cand, blob, when = vs[nth - 1]
    raw = subprocess.run(["git", f"--git-dir={store_of(project)}", "cat-file", "-p", blob],
                         capture_output=True).stdout
    if os.path.exists(target):
        outdir = os.path.join(project, "_もどしたもの")
        os.makedirs(outdir, exist_ok=True)
        out = os.path.join(outdir, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(target))
        with open(out, "wb") as f:
            f.write(raw)
        print(f"✅ いまの ファイルは 上書きせず、{when} の 版を 横に 出しました: {os.path.relpath(out, project)}")
        print("   中身を 見比べて、良ければ 置き換えて ください（Claude に「_もどしたもの の 版で 置き換えて」と 頼んでも よい）。")
        if len(vs) > nth:
            print(f"   もっと 前の 版＝ 戻す {args[0]} {nth + 1}（いまと 違う 版は 全部で {len(vs)} 個）")
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(raw)
        print(f"✅ 戻しました（{when} の 控え）: {rel}")
        if len(vs) > nth:
            print(f"   ちがう 版が 欲しい 時＝ 戻す {args[0]} {nth + 1}（版は 全部で {len(vs)} 個）")
    return 0


def cmd_list(args):
    start = project_of(args[0] if args else None)
    project = find_project(start)
    if not project:
        print(NOT_IN.format(start))
        return 1
    if not has_commits(project):
        print("（まだ 控えが ありません）")
        return 0
    log = git(project, "log", "-12", "--format=@@%h %s", "--name-status").stdout
    print(f"📁 {project} の 控え（新しい 順に 最新の 12 回 分を 表示）")
    for block in log.split("@@")[1:]:
        lines = [l for l in block.splitlines() if l.strip()]
        head, rest = lines[0], lines[1:]
        gone = [l.split("\t", 1)[1] for l in rest if l.startswith("D\t")]
        print(f"  ・{head[8:]}（{len(rest)} ファイル）" + (f"　⚠️ 消えた: {', '.join(gone[:5])}" if gone else ""))
    print("戻す ときは: /usr/bin/python3 ~/.modoseru/modoseru.py 戻す <ファイル>")
    return 0


def settings_path(project):
    return os.path.join(project, ".claude", "settings.local.json")


def load_settings(project):
    sp = settings_path(project)
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            return json.load(f)
    return {}


def hook_cmd(sub, memo=""):
    exe = os.path.join(tool_dir(), "modoseru.py")
    tail = f' "{memo}"' if memo else ""
    return f'/usr/bin/python3 "{exe}" {sub} --フック{tail} 2>/dev/null || true'


def our_hooks():
    return {
        "SessionStart": [{"hooks": [{"type": "command", "command": hook_cmd("点検"), "timeout": 30}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": hook_cmd("控える", "Bash の 前"), "timeout": 60}]}],
        "PostToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash", "hooks": [{"type": "command", "command": hook_cmd("控える", "書いた 後"), "timeout": 60}]}],
    }


def is_ours(entry):
    return any(MARK in h.get("command", "") for h in entry.get("hooks", []))


def merge_settings(project):
    s = load_settings(project)
    hooks = s.setdefault("hooks", {})
    for ev, entries in our_hooks().items():
        cur = [e for e in hooks.get(ev, []) if not is_ours(e)]
        hooks[ev] = cur + entries
    perms = s.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    added = [d for d in DENY if d not in deny]
    deny += added
    return s, added


def write_settings(project, s):
    sp = settings_path(project)
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    tmp = sp + ".modoseru-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(tmp, encoding="utf-8") as f:
        json.load(f)  # 読み戻せる ことを 確かめて から 置く
    os.replace(tmp, sp)


def cmd_install(args):
    project = project_of(args[0] if args else os.getcwd())
    if project in (home(), "/", nfc(os.path.realpath(home()))):
        print("❌ ホーム や / 全体には 入れません。作業フォルダを 1 つ 指定して ください。")
        return 1
    if not shutil.which("git"):
        print("❌ git が ありません。ターミナルで `xcode-select --install` を 一度 実行して から もう 一度。")
        return 1
    os.makedirs(tool_dir(), exist_ok=True)
    me = os.path.realpath(__file__)
    dst = os.path.join(tool_dir(), "modoseru.py")
    if me != os.path.realpath(dst):
        shutil.copyfile(me, dst)
    init_store(project)
    sp = settings_path(project)
    rec_path = store_of(project) + ".入れた記録.json"
    before = None
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            before = f.read()
        bk = sp + ".もどせる前"
        if not os.path.exists(bk):
            with open(bk, "w", encoding="utf-8") as f:
                f.write(before)
    s, added = merge_settings(project)
    write_settings(project, s)
    if os.path.exists(rec_path):  # 前に 入れた 時に 足した 拒否も 覚えて おく（外す 時に 外す ため）
        with open(rec_path, encoding="utf-8") as f:
            prev = json.load(f).get("足した拒否", [])
        added = prev + [d for d in added if d not in prev]
    rec = {"版": VERSION, "作業フォルダ": project, "足した拒否": added,
           "入れた日": time.strftime("%Y-%m-%d %H:%M:%S"), "前に設定が在った": before is not None}
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    n = snapshot(project, "入れた 時の 最初の 控え")
    print(f"✅ もどせる {VERSION} を 入れました: {project}")
    print(f"   ・最初の 控え＝{n} ファイル（テキストの ファイル だけ）")
    print("   ・Claude Code が Bash を 走らせる 前と、ファイルを 書いた 後に 自動で 控えます")
    print(f"   ・危ない 命令を 止める 設定＝{len(DENY)} 本 入って います（rm -rf・git reset --hard など）")
    print("   ・Claude Code を 開き直すと、回の 頭に 点検の 結果が 出ます")
    print("   消えた ときは: /usr/bin/python3 ~/.modoseru/modoseru.py 戻す <ファイル>")
    print("   外す ときは:   /usr/bin/python3 ~/.modoseru/modoseru.py 外す")
    return 0


def cmd_uninstall(args):
    project = project_of(args[0] if args else None)
    rec_path = store_of(project) + ".入れた記録.json"
    added = []
    if os.path.exists(rec_path):
        with open(rec_path, encoding="utf-8") as f:
            added = json.load(f).get("足した拒否", [])
    s = load_settings(project)
    hooks = s.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [e for e in hooks[ev] if not is_ours(e)]
        if not hooks[ev]:
            del hooks[ev]
    if "hooks" in s and not s["hooks"]:
        del s["hooks"]
    deny = s.get("permissions", {}).get("deny")
    if deny is not None:
        s["permissions"]["deny"] = [d for d in deny if d not in added]
        if not s["permissions"]["deny"]:
            del s["permissions"]["deny"]
        if not s["permissions"]:
            del s["permissions"]
    write_settings(project, s)
    print(f"✅ もどせる の フックと 足した 拒否を 外しました: {project}")
    print(f"   控え（{store_of(project)}）は 残して あります（要らなければ ご自分で ゴミ箱へ）")
    return 0


def check(project, quiet_ok=False):
    """回の 頭の 自己点検。返り値＝(❌ の 数, 行の 一覧)"""
    rows = []

    def row(ok, name, msg):
        rows.append(("✅" if ok else "❌", name, msg))

    row(bool(shutil.which("git")), "git", "在る" if shutil.which("git") else "無い＝xcode-select --install")
    s = load_settings(project)
    hooks = s.get("hooks", {})
    for ev in ("SessionStart", "PreToolUse", "PostToolUse"):
        ok = any(is_ours(e) for e in hooks.get(ev, []))
        row(ok, f"フック {ev}", "入って いる" if ok else "外れて いる＝入れ直す")
    deny = s.get("permissions", {}).get("deny", [])
    miss = [d for d in DENY if d not in deny]
    row(not miss, "危ない 命令の 止め", f"{len(DENY) - len(miss)}/{len(DENY)} 本" + (f"（無い: {', '.join(miss)}）" if miss else ""))
    g = store_of(project)
    if os.path.isdir(g) and has_commits(project):
        t = int(git(project, "log", "-1", "--format=%ct").stdout.strip() or 0)
        age_m = int((time.time() - t) / 60)
        ago = f"{age_m} 分 前" if age_m < 120 else f"{age_m // 60} 時間 前"
        cnt = git(project, "rev-list", "--count", "HEAD").stdout.strip()
        row(True, "控え", f"{cnt} 回 分・いちばん 新しい 控えは {ago}")
    else:
        row(False, "控え", "まだ 無い＝入れ直す")
    try:
        st = os.statvfs(project)
        free_gb = st.f_bavail * st.f_frsize / 1024 ** 3
        row(free_gb >= 10, "ディスクの 空き", f"{free_gb:.1f} GB" + ("" if free_gb >= 10 else "＝少ない（控えも 書けなく なる）"))
    except OSError:
        row(False, "ディスクの 空き", "測れない")
    bad = sum(1 for r in rows if r[0] == "❌")
    return bad, rows


def cmd_check(args):
    hookmode = "--フック" in args
    rest = [a for a in args if a != "--フック"]
    project = project_of(rest[0] if rest else None)
    if not hookmode:
        project = find_project(project) or project
    if hookmode:
        snapshot(project, "回の 頭")
    bad, rows = check(project)
    print(f"【もどせる の 点検】{project}")
    for mark, name, msg in rows:
        print(f"  {mark} {name}：{msg}")
    print("  ⇒ " + ("全部 ✅" if bad == 0 else f"❌ {bad} 件＝作業の 前に 直して ください（Claude に この 行を 見せれば 直し方を 案内できます）"))
    return 0 if (hookmode or bad == 0) else 1


def cmd_snapshot(args):
    hookmode = "--フック" in args
    rest = [a for a in args if a != "--フック"]
    memo = rest[0] if rest else "手で 控えた"
    project = project_of(None)
    if not hookmode:
        found = find_project(project)
        if not found:
            print(NOT_IN.format(project))
            return 1
        project = found
    if hookmode:
        try:
            data = json.loads(sys.stdin.read() or "{}")
            if not os.environ.get("CLAUDE_PROJECT_DIR") and data.get("cwd"):
                project = project_of(data["cwd"])
        except (ValueError, OSError):
            pass
    try:
        n = snapshot(project, memo)
    except Exception as e:  # フックで Claude を 止めない
        if not hookmode:
            print(f"❌ 控えに 失敗: {e}")
            return 1
        return 0
    if not hookmode:
        print(f"✅ 控えました（{n} ファイル）" if n else "（変わりが 無いので 控えは 増やして いません）")
    return 0


# ───────────── 自己検査（床＝平常で 鳴らない／天井＝事故で 鳴る） ─────────────
def self_test():
    import tempfile
    results = []

    def ok(cond, name):
        results.append((bool(cond), name))

    base = tempfile.mkdtemp(prefix="modoseru_test_")
    fake_home = os.path.join(base, "home")
    proj = os.path.join(base, "作業フォルダ")
    os.makedirs(fake_home)
    os.makedirs(proj)
    env = dict(os.environ, HOME=fake_home)
    env.pop("CLAUDE_PROJECT_DIR", None)
    me = os.path.realpath(__file__)

    def run(*a, cwd=proj, stdin=""):
        exe = os.path.join(fake_home, ".modoseru", "modoseru.py")
        exe = exe if os.path.exists(exe) else me
        return subprocess.run(["/usr/bin/python3", exe] + list(a), cwd=cwd, env=env,
                              capture_output=True, text=True, input=stdin)

    # 前から 在る 設定（ご本人の フックと 拒否）＝入れても 外しても 残るか
    os.makedirs(os.path.join(proj, ".claude"))
    orig = {"permissions": {"deny": ["Bash(sudo:*)"], "allow": ["Bash(ls:*)"]},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                      "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}]}}
    with open(os.path.join(proj, ".claude", "settings.local.json"), "w", encoding="utf-8") as f:
        json.dump(orig, f)
    with open(os.path.join(proj, "メモ.md"), "w", encoding="utf-8") as f:
        f.write("だいじな メモ 1\n")
    os.makedirs(os.path.join(proj, "下"))
    with open(os.path.join(proj, "下", "台本.py"), "w", encoding="utf-8") as f:
        f.write("print('hi')\n")
    with open(os.path.join(proj, "写真.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff" + b"0" * 100)

    r = run("入れる", proj)
    ok(r.returncode == 0 and "入れました" in r.stdout, "床: 入れる が 通る")
    s = json.load(open(os.path.join(proj, ".claude", "settings.local.json"), encoding="utf-8"))
    ok(any("echo mine" in h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]), "床: 前から 在る Stop の フックが 残る")
    ok(any("echo guard" in h["command"] for e in s["hooks"]["PreToolUse"] for h in e["hooks"]), "床: 前から 在る PreToolUse の フックが 残る")
    ok("Bash(sudo:*)" in s["permissions"]["deny"] and "Bash(ls:*)" in s["permissions"]["allow"], "床: 前から 在る 拒否と 許可が 残る")
    ok(all(d in s["permissions"]["deny"] for d in DENY), "床: 拒否 4 本が 入る")
    r = run("入れる", proj)
    s2 = json.load(open(os.path.join(proj, ".claude", "settings.local.json"), encoding="utf-8"))
    ok(sum(1 for e in s2["hooks"]["PreToolUse"] if is_ours(e)) == 1 and s2["permissions"]["deny"].count(DENY[0]) == 1,
       "床: 2 回 入れても フックと 拒否が 重ならない")
    r = run("点検", proj)
    ok(r.returncode == 0 and "全部 ✅" in r.stdout, "床: 点検 が 全部 ✅")

    # 控えの 中身
    tracked = subprocess.run(["git", "-c", "core.quotepath=false", f"--git-dir={store_of_for(proj, fake_home)}", "ls-files"],
                             cwd=proj, capture_output=True, text=True).stdout
    ok("メモ.md" in tracked and "下/台本.py" in tracked, "床: テキストは 控える")
    ok("写真.jpg" not in tracked, "天井: 写真（.jpg）は 控えない")

    # Bash の 前の フック → rm → 戻す
    hook_in = json.dumps({"cwd": proj, "tool_name": "Bash"})
    with open(os.path.join(proj, "メモ.md"), "w", encoding="utf-8") as f:
        f.write("だいじな メモ 2（Bash の 直前に 書いた）\n")
    r = run("控える", "--フック", "Bash の 前", stdin=hook_in)
    ok(r.returncode == 0, "床: フックの 控える が 0 で 返る")
    os.remove(os.path.join(proj, "メモ.md"))
    shutil.rmtree(os.path.join(proj, "下"))
    run("控える", "--フック", "書いた 後", stdin=hook_in)
    r = run("戻す", "メモ.md")
    ok(r.returncode == 0 and os.path.exists(os.path.join(proj, "メモ.md")), "床: 消えた ファイルが 戻る")
    ok(open(os.path.join(proj, "メモ.md"), encoding="utf-8").read() == "だいじな メモ 2（Bash の 直前に 書いた）\n",
       "床: 戻った 中身が 消える 直前の 版")
    r = run("戻す", "下/台本.py")
    ok(r.returncode == 0 and open(os.path.join(proj, "下", "台本.py"), encoding="utf-8").read() == "print('hi')\n",
       "床: 消えた フォルダの 中の ファイルも 戻る")
    r = run("戻す", "メモ.md")  # いまの 版（メモ 2）は 数えない＝1 は メモ 1
    ok(r.returncode == 0 and "横に 出しました" in r.stdout, "床: 在る ファイルは 上書き せず 横に 出す")
    ok(open(os.path.join(proj, "メモ.md"), encoding="utf-8").read().startswith("だいじな メモ 2"),
       "床: 横に 出しても いまの ファイルは 変わらない")
    # 上書きの 事故＝命令の 後の 控えで いまの 中身が 最新に なっても、数 なしで 1 つ 前の 版が 出る
    with open(os.path.join(proj, "しおり.md"), "w", encoding="utf-8") as f:
        f.write("1 日目\n2 日目\n")
    run("控える", "--フック", "Bash の 前", stdin=hook_in)
    with open(os.path.join(proj, "しおり.md"), "w", encoding="utf-8") as f:
        f.write("まちがい\n")
    run("控える", "--フック", "書いた 後", stdin=hook_in)
    r = run("戻す", "しおり.md")
    outs = [x for x in os.listdir(os.path.join(proj, "_もどしたもの")) if x.endswith("しおり.md")]
    ok(r.returncode == 0 and outs and open(os.path.join(proj, "_もどしたもの", sorted(outs)[-1]), encoding="utf-8").read() == "1 日目\n2 日目\n",
       "床: 上書きの 後 数 なしで 上書き 前の 版が 出る（いまと 同じ 版は 数えない）")
    r = run("控える", "手", cwd=base)
    ok(r.returncode == 1 and "入って いません" in r.stdout, "天井: 入って いない フォルダで 控える と 分かる 字で ❌（「変わりが 無い」と 言わない）")
    with open(os.path.join(proj, "根の.md"), "w", encoding="utf-8") as f:
        f.write("根の 変化\n")
    r = run("控える", "下から", cwd=os.path.join(proj, "下"))
    tracked = subprocess.run(["git", "-c", "core.quotepath=false", f"--git-dir={store_of_for(proj, fake_home)}", "ls-files"],
                             cwd=proj, capture_output=True, text=True).stdout
    ok(r.returncode == 0 and "根の.md" in tracked, "床: 下の フォルダから 控えても 根の 変化が 入る")
    r = run("一覧", cwd=os.path.join(proj, "下"))
    ok(r.returncode == 0 and "の 控え" in r.stdout, "床: 作業フォルダの 下の フォルダから 一覧 が 引ける")
    r = run("戻す", "無い.md")
    ok(r.returncode == 1 and "見つかりません" in r.stdout, "天井: 控えに 無い ファイルは ❌ で 止まる")
    r = run("戻す", "メモ.md", "99")
    ok(r.returncode == 1, "天井: 何個前 が 多すぎると ❌")
    r = run("一覧", proj)
    ok("消えた" in r.stdout and "メモ.md" in r.stdout, "床: 一覧 に 消えた ファイルが 出る")

    # 大きい ファイル
    with open(os.path.join(proj, "大きい.txt"), "w") as f:
        f.write("a" * (5 * 1024 * 1024 + 10))  # 約束の 字（5 MB）で 固定＝MAX_BYTES が 動いたら 鳴る
    run("控える", "手")
    tracked = subprocess.run(["git", "-c", "core.quotepath=false", f"--git-dir={store_of_for(proj, fake_home)}", "ls-files"],
                             cwd=proj, capture_output=True, text=True).stdout
    ok("大きい.txt" not in tracked, "天井: 5 MB 超は 控えない")

    # 点検の 天井＝フックを 外したら ❌
    s3 = json.load(open(os.path.join(proj, ".claude", "settings.local.json"), encoding="utf-8"))
    s3["hooks"]["PreToolUse"] = [e for e in s3["hooks"]["PreToolUse"] if not is_ours(e)]
    with open(os.path.join(proj, ".claude", "settings.local.json"), "w", encoding="utf-8") as f:
        json.dump(s3, f)
    r = run("点検", proj)
    ok(r.returncode == 1 and "❌ フック PreToolUse" in r.stdout, "天井: フックが 外れたら 点検が ❌")
    r = run("点検", "--フック", proj)
    ok(r.returncode == 0 and "❌" in r.stdout, "床: フックで 呼ぶ 点検は ❌ でも 0 で 返る（Claude を 止めない）")
    run("入れる", proj)

    # 外す＝元の 設定に 戻る
    r = run("外す", proj)
    s4 = json.load(open(os.path.join(proj, ".claude", "settings.local.json"), encoding="utf-8"))
    ok(s4 == orig, "床: 外すと 前の 設定と 同じに 戻る")

    # ホームには 入れない
    r = run("入れる", fake_home, cwd=fake_home)
    ok(r.returncode == 1, "天井: ホームには 入れない")

    floor = [r for r in results if r[1].startswith("床")]
    ceil = [r for r in results if r[1].startswith("天井")]
    for good, name in results:
        print(("  ✅ " if good else "  ❌ ") + name)
    nbad = sum(1 for g, _ in results if not g)
    print(f"⭐ もどせる の 自己検査 ── ✅ {len(results) - nbad} / ❌ {nbad}（床 {len(floor)}・天井 {len(ceil)}）・検体＝{base}")
    return 0 if nbad == 0 else 1


def store_of_for(project, fake_home):
    h = hashlib.sha1(nfc(os.path.realpath(project)).encode("utf-8")).hexdigest()[:10]
    name = os.path.basename(project.rstrip("/"))
    return os.path.join(fake_home, ".modoseru", "控え", f"{name}-{h}.git")


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "使い方"):
        print(__doc__)
        return 0
    if argv[0] == "--自己検査":
        return self_test()
    table = {"入れる": cmd_install, "戻す": cmd_restore, "一覧": cmd_list, "点検": cmd_check,
             "外す": cmd_uninstall, "控える": cmd_snapshot}
    f = table.get(argv[0])
    if not f:
        print(f"❌ 知らない 言葉: {argv[0]}（入れる・戻す・一覧・点検・外す）")
        return 1
    return f(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
