from datetime import timedelta

def format_time(seconds):
    """Formats seconds into 00:00:00:00 string."""
    return str(timedelta(seconds=int(seconds))).zfill(8)

def generate_progress_bar(percentage):
    """Generates the text-based progress bar."""
    total_segments = 15
    completed = int((max(0, min(100, percentage)) / 100) * total_segments)
    return "[" + "▰" * completed + "▱" * (total_segments - completed) + "]"

def select_params(height):
    """Determines default CRF and Preset based on resolution height."""
    if height >= 2000: return 32, 10
    elif height >= 1000: return 42, 6
    elif height >= 700: return 24, 6
    return 22, 4

def calculate_stats(line, start_time, duration, total_frames):
    """Parses FFmpeg log line and calculates speed, fps, eta."""
    try:
        import time
        curr_sec = int(line.split("=")[1]) / 1_000_000
        percent = (curr_sec / duration) * 100
        elapsed = time.time() - start_time
        speed = curr_sec / elapsed if elapsed > 0 else 0
        fps = (percent / 100 * total_frames) / elapsed if elapsed > 0 else 0
        eta = (elapsed / percent) * (100 - percent) if percent > 0 else 0
        return curr_sec, percent, speed, fps, eta
    except:
        return None