import os
import re
import sys
import time
from dataclasses import dataclass

# Доступ к реестру на Windows
try:
    import winreg
except ImportError:
    winreg = None

'''
Вспомогательные функции
'''

# Функция для определения скорости в привычном для человека виде
def real_speed(bytes_per_sec):
    if bytes_per_sec < 0:
        bytes_per_sec = 0.0

    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    val = float(bytes_per_sec)
    i = 0

    while val >= 1024 and i < len(units) - 1:
        val /= 1024.0
        i += 1

    return f"{val:.2f} {units[i]}"

# Функция для нормализации пути
def safe_realpath(p):
    try:
        return os.path.realpath(p)
    except Exception:
        return p

# Рекурсивная функция для расчета размера папки
def folder_size_bytes(path):
    total = 0
    stack = [path]

    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except Exception:
                        pass
        except Exception:
            pass

    return total

# Функция, которая читает последние символы в файле
def last_file_symbols(path, max_bytes = 200_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""

# Функция, которая читает значение из реестра (Windows)
def read_registry_value(root, subkey, value_name):
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, subkey) as k:
            val, _ = winreg.QueryValueEx(k, value_name)
            if isinstance(val, str) and val.strip():
                return val
    except Exception:
        return None

    return None

# Функция, которая определяет корневую папку Steam
def find_steam_path():
    plat = sys.platform.lower()

    # macOS
    if plat == "darwin":
        candidates = [
            os.path.expanduser("~/Library/Application Support/Steam"),
        ]
        for p in candidates:
            p = safe_realpath(p)
            if os.path.isdir(p) and os.path.isdir(os.path.join(p, "steamapps")):
                return p

        return None

    # Linux
    if plat.startswith("linux"):
        candidates = [
            os.path.expanduser("~/.steam/steam"),
            os.path.expanduser("~/.local/share/Steam"),
            os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam"),
        ]
        for p in candidates:
            p = safe_realpath(p)
            if os.path.isdir(p) and os.path.isdir(os.path.join(p, "steamapps")):
                return p
        return None

    # Windows
    if winreg is not None:
        candidates = [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
        ]
        for root, subkey, value_name in candidates:
            p = read_registry_value(root, subkey, value_name)
            if p:
                p = safe_realpath(p)
                if os.path.isdir(p) and os.path.isdir(os.path.join(p, "steamapps")):
                    return p

        fallback = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Steam"),
            os.path.expandvars(r"%ProgramFiles%\Steam"),
        ]
        for p in fallback:
            if p:
                p = safe_realpath(p)
                if os.path.isdir(p) and os.path.isdir(os.path.join(p, "steamapps")):
                    return p

    return None


_kv_re = re.compile(r'^\s*"([^"]+)"\s*"([^"]*)"\s*$')

def parse_key_value_file(path):
    out = {}
    
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _kv_re.match(line)
                if m:
                    out[m.group(1)] = m.group(2)
    except Exception:
        pass
    return out


def parse_libraryfolders_vdf(path):
    libs = []

    if not os.path.isfile(path):
        return libs

    try:
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return libs

    # Новый формат -  "path" "X:\Something"
    for m in re.finditer(r'"\s*path\s*"\s*"([^"]+)"', text, flags=re.IGNORECASE):
        p = m.group(1).replace("\\\\", "\\")
        p = safe_realpath(p)
        if p and os.path.isdir(p):
            libs.append(p)

    # Старый формат: "1" "X:\Something"
    for m in re.finditer(r'"\s*\d+\s*"\s*"([^"]+)"', text):
        p = m.group(1).replace("\\\\", "\\")
        p = safe_realpath(p)
        if p and os.path.isdir(p):
            libs.append(p)

    # Убираем дубликаты
    seen = set()
    out = []
    for p in libs:
        if p not in seen:
            out.append(p)
            seen.add(p)

    return out


def get_all_steamapps_folders(steam_path):
    out = []
    main_sa = os.path.join(steam_path, "steamapps")
    if os.path.isdir(main_sa):
        out.append(safe_realpath(main_sa))

    lib_vdf = os.path.join(main_sa, "libraryfolders.vdf")
    for lib in parse_libraryfolders_vdf(lib_vdf):
        sa = os.path.join(lib, "steamapps")
        if os.path.isdir(sa):
            out.append(safe_realpath(sa))

    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq

@dataclass
class DownloadInfo:
    appid: int
    name: str
    status: str     # downloading | paused | unknown
    speed_bps: float


def resolve_app_name(appid, steamapps_folders):
    manifest = f"appmanifest_{appid}.acf"

    for sa in steamapps_folders:
        p = os.path.join(sa, manifest)
        if os.path.isfile(p):
            kv = parse_key_value_file(p)
            name = kv.get("name")
            if name:
                return name

    return f"AppID {appid}"


def detect_appid_from_downloading_dir(steamapps_folders):
    candidates = []

    for sa in steamapps_folders:
        downloading = os.path.join(sa, "downloading")
        if not os.path.isdir(downloading):
            continue
        try:
            with os.scandir(downloading) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False) and entry.name.isdigit():
                        appid = int(entry.name)
                        try:
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                        except Exception:
                            mtime = 0.0
                        candidates.append((mtime, appid))
        except Exception:
            pass

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def get_downloading_folder(steamapps_folders, appid):
    for sa in steamapps_folders:
        p = os.path.join(sa, "downloading", str(appid))
        if os.path.isdir(p):
            return p
    return None


def detect_status_from_content_log(steam_path, appid):
    log_path = os.path.join(steam_path, "logs", "content_log.txt")
    if not os.path.isfile(log_path):
        return None

    text = last_file_symbols(log_path)
    lines = text.splitlines()

    if appid is not None:
        s = str(appid)
        lines = [ln for ln in lines if s in ln]

    for ln in reversed(lines[-800:]):
        low = ln.lower()
        if "pause" in low:
            return "paused"
        if "downloading" in low or "download" in low:
            if "pause" not in low:
                return "downloading"

    return None

def monitor_steam_downloads(duration_minutes = 5, interval_seconds = 60):
    steam_path = find_steam_path()
    if not steam_path:
        print("Steam не найден: не удалось определить путь установки.")
        print("Убедись, что Steam установлен и существует папка steamapps.")
        return

    steamapps_folders = get_all_steamapps_folders(steam_path)
    if not steamapps_folders:
        print(f"Steam найден: {steam_path}")
        print("Но папка steamapps не найдена (или недоступна).")
        return

    print(f"Steam найден: {steam_path}")
    print("Библиотеки (steamapps):")
    for sa in steamapps_folders:
        print(f"  - {sa}")

    last_size = None
    last_appid = None
    last_time = time.time()

    ticks = duration_minutes
    zero_streak = 0

    for minute_idx in range(1, ticks + 1):
        appid = detect_appid_from_downloading_dir(steamapps_folders)

        if appid is None:
            print(f"[{minute_idx}/{ticks}] Сейчас активной загрузки не обнаружено.")
            last_size = None
            last_appid = None
            time.sleep(interval_seconds)
            continue

        name = resolve_app_name(appid, steamapps_folders)
        dl_folder = get_downloading_folder(steamapps_folders, appid)

        status = detect_status_from_content_log(steam_path, appid) or "unknown"

        now = time.time()
        speed_bps = 0.0

        if dl_folder:
            cur_size = folder_size_bytes(dl_folder)

            if last_size is not None and last_appid == appid:
                dt = max(1e-6, now - last_time)
                speed_bps = (cur_size - last_size) / dt

                if cur_size == last_size and status == "unknown":
                    status = "paused"

            last_size = cur_size
        else:
            last_size = None

        last_time = now
        last_appid = appid

        if abs(speed_bps) < 1024:  # < 1 KB/s считаем как "0"
            zero_streak += 1
        else:
            zero_streak = 0

        # Если скорость < 1 KB/s 2 мин подряд — считаем паузой
        if zero_streak >= 2:
            status = "paused"

        if abs(speed_bps) < 1 and status == "unknown":
            status = "paused"

        print(f"[{minute_idx}/{ticks}] Игра: {name} | Статус: {status} | Скорость: {real_speed(speed_bps)}")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    monitor_steam_downloads(duration_minutes=5, interval_seconds=60)
