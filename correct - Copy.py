import sys
import numpy as np
import cv2
import math
import os
import subprocess
import imageio_ffmpeg
from PIL import Image
import threading
import queue

THRESHOLD_RATIO = 2000
BLUE_MAGIC_VALUE = 1.2
SAMPLE_SECONDS = 2 # Extracts color correction from every N seconds

def load_cube_lut(filepath):
    """
    Parses a .cube file and returns a pre-baked 256x256x256 lookup table (uint8),
    so that applying the LUT to any image is just a fast numpy index lookup.
    """
    with open(filepath, 'r') as f:
        lines = f.readlines()

    size = 0
    data = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('LUT_3D_SIZE'):
            size = int(line.split()[1])
        elif line[0].isdigit() or line[0] == '.' or line[0] == '-':
            parts = line.split()
            if len(parts) == 3:
                data.append([float(parts[0]), float(parts[1]), float(parts[2])])

    if size == 0 or len(data) != size * size * size:
        raise ValueError(f"Invalid or unsupported .cube file: {filepath}")

    # Raw LUT: shape (size, size, size, 3), indexed [B, G, R], values in [0,1]
    lut_raw = np.array(data, dtype=np.float32).reshape((size, size, size, 3))

    # Pre-bake to a full 256x256x256 table via trilinear interpolation at load time.
    # This is done once per LUT, making per-frame application essentially free.
    scale = (size - 1) / 255.0
    idx = np.arange(256, dtype=np.float32) * scale          # shape (256,)

    # Integer and fractional parts for each channel
    i0 = np.floor(idx).astype(np.int32)
    i1 = np.clip(i0 + 1, 0, size - 1)
    f  = (idx - i0).astype(np.float32)                      # shape (256,)

    # Build meshgrid over [R, G, B] — output shape (256, 256, 256)
    # axes: [b_idx, g_idx, r_idx]
    r0, g0, b0 = np.meshgrid(i0, i0, i0, indexing='ij')    # (256,256,256)
    r1, g1, b1 = np.meshgrid(i1, i1, i1, indexing='ij')
    fr, fg, fb = np.meshgrid(f,  f,  f,  indexing='ij')    # fractions

    # Trilinear interpolation across all 256^3 entries at once
    c000 = lut_raw[b0, g0, r0]
    c100 = lut_raw[b0, g0, r1]
    c010 = lut_raw[b0, g1, r0]
    c110 = lut_raw[b0, g1, r1]
    c001 = lut_raw[b1, g0, r0]
    c101 = lut_raw[b1, g0, r1]
    c011 = lut_raw[b1, g1, r0]
    c111 = lut_raw[b1, g1, r1]

    fr = fr[..., np.newaxis]
    fg = fg[..., np.newaxis]
    fb = fb[..., np.newaxis]

    c00 = c000 * (1 - fr) + c100 * fr
    c10 = c010 * (1 - fr) + c110 * fr
    c01 = c001 * (1 - fr) + c101 * fr
    c11 = c011 * (1 - fr) + c111 * fr

    c0  = c00  * (1 - fg) + c10  * fg
    c1  = c01  * (1 - fg) + c11  * fg

    baked = c0 * (1 - fb) + c1 * fb          # shape (256, 256, 256, 3)
    baked = np.clip(baked * 255.0, 0, 255).astype(np.uint8)
    return baked  # indexed [r, g, b] → output [R', G', B']


def apply_3d_lut(image, lut_baked):
    """
    Applies a pre-baked 256x256x256 LUT to an RGB image.
    Extremely fast: just two numpy index operations.
    """
    r = image[..., 0].astype(np.uint8)
    g = image[..., 1].astype(np.uint8)
    b = image[..., 2].astype(np.uint8)
    return lut_baked[r, g, b]
    #return lut_baked[b, g, r]

def hue_shift_red(mat, h):

    U = math.cos(h * math.pi / 180)
    W = math.sin(h * math.pi / 180)

    r = (0.299 + 0.701 * U + 0.168 * W) * mat[..., 0]
    g = (0.587 - 0.587 * U + 0.330 * W) * mat[..., 1]
    b = (0.114 - 0.114 * U - 0.497 * W) * mat[..., 2]

    return np.dstack([r, g, b])

def normalizing_interval(array):

    high = 255
    low = 0
    max_dist = 0

    for i in range(1, len(array)):
        dist = array[i] - array[i-1]
        if(dist > max_dist):
            max_dist = dist
            high = array[i]
            low = array[i-1]

    return (low, high)

def apply_filter(mat, filt):
    transform_matrix = np.array([
        [filt[0], filt[1], filt[2], filt[4] * 255],
        [0,       filt[6], 0,       filt[9] * 255],
        [0,       0,       filt[12], filt[14] * 255]
    ], dtype=np.float32)

    filtered_mat = cv2.transform(mat, transform_matrix)
    return filtered_mat

def apply_clahe_to_rgb(mat, clahe_clip=2.0):
    if mat.dtype != np.uint8:
        mat = np.clip(mat, 0, 255).astype(np.uint8)
        
    lab = cv2.cvtColor(mat, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(8,8))
    cl = clahe.apply(l)
    
    merged = cv2.merge((cl, a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)

def get_filter_matrix(mat, use_clahe=False, min_avg_red=60, max_hue_shift=120):

    mat = cv2.resize(mat, (256, 256))

    avg_mat = np.array(cv2.mean(mat)[:3], dtype=np.uint8)
    
    new_avg_r = avg_mat[0]
    hue_shift = 0
    while(new_avg_r < min_avg_red):

        shifted = hue_shift_red(avg_mat, hue_shift)
        new_avg_r = np.sum(shifted)
        hue_shift += 1
        if hue_shift > max_hue_shift:
            new_avg_r = min_avg_red

    shifted_mat = hue_shift_red(mat, hue_shift)
    new_r_channel = np.sum(shifted_mat, axis=2)
    new_r_channel = np.clip(new_r_channel, 0, 255)
    mat[..., 0] = new_r_channel

    hist_r = cv2.calcHist([mat], [0], None, [256], [0,256])
    hist_g = cv2.calcHist([mat], [1], None, [256], [0,256])
    hist_b = cv2.calcHist([mat], [2], None, [256], [0,256])

    normalize_mat = np.zeros((256, 3))
    threshold_level = (mat.shape[0]*mat.shape[1])/THRESHOLD_RATIO
    for x in range(256):
        
        if hist_r[x] < threshold_level:
            normalize_mat[x][0] = x

        if hist_g[x] < threshold_level:
            normalize_mat[x][1] = x

        if hist_b[x] < threshold_level:
            normalize_mat[x][2] = x

    normalize_mat[255][0] = 255
    normalize_mat[255][1] = 255
    normalize_mat[255][2] = 255

    if use_clahe:
        adjust_r_low, adjust_r_high = 0, 256
        adjust_g_low, adjust_g_high = 0, 256
        adjust_b_low, adjust_b_high = 0, 256
    else:
        adjust_r_low, adjust_r_high = normalizing_interval(normalize_mat[..., 0])
        adjust_g_low, adjust_g_high = normalizing_interval(normalize_mat[..., 1])
        adjust_b_low, adjust_b_high = normalizing_interval(normalize_mat[..., 2])


    shifted = hue_shift_red(np.array([1, 1, 1]), hue_shift)
    shifted_r, shifted_g, shifted_b = shifted[0][0]

    red_gain = 256 / (adjust_r_high - adjust_r_low)
    green_gain = 256 / (adjust_g_high - adjust_g_low)
    blue_gain = 256 / (adjust_b_high - adjust_b_low)

    redOffset = (-adjust_r_low / 256) * red_gain
    greenOffset = (-adjust_g_low / 256) * green_gain
    blueOffset = (-adjust_b_low / 256) * blue_gain

    adjust_red = shifted_r * red_gain
    adjust_red_green = shifted_g * red_gain
    adjust_red_blue = shifted_b * red_gain * BLUE_MAGIC_VALUE

    return np.array([
        adjust_red, adjust_red_green, adjust_red_blue, 0, redOffset,
        0, green_gain, 0, 0, greenOffset,
        0, 0, blue_gain, 0, blueOffset,
        0, 0, 0, 1, 0,
    ])

def correct(mat, use_clahe=False, min_avg_red=60, max_hue_shift=120, clahe_clip=2.0, use_saturation=False, saturation_val=1.0, use_brightness=False, brightness_val=0, contrast_val=1.0, luts=None, lut_only=False, ops_order=None):
    original_mat = mat.copy()
    corrected_mat = original_mat.copy()

    if ops_order is None:
        ops_order = ["Dive Color", "CLAHE", "Brightness", "Contrast", "Red Strength", "LUTs", "Hue Shift", "Saturation"]

    if lut_only:
        if luts:
            for lut_array in luts:
                corrected_mat = apply_3d_lut(corrected_mat, lut_array)
    else:
        # We need to handle Dive Color, Red Strength, and Hue Shift together as they are part of the filter matrix
        # For simplicity, if any of them are in the ops_order, we apply the filter matrix at the position of the first one found
        filter_applied = False
        
        for op in ops_order:
            if op in ["Dive Color", "Red Strength", "Hue Shift"] and not filter_applied:
                filter_matrix = get_filter_matrix(mat, use_clahe, min_avg_red, max_hue_shift)
                corrected_mat = apply_filter(corrected_mat, filter_matrix)
                filter_applied = True
            elif op == "Brightness" and use_brightness and brightness_val != 0:
                corrected_mat = cv2.convertScaleAbs(corrected_mat, alpha=1.0, beta=brightness_val)
            elif op == "Contrast" and use_brightness and contrast_val != 1.0:
                corrected_mat = cv2.convertScaleAbs(corrected_mat, alpha=contrast_val, beta=0)
            elif op == "CLAHE" and use_clahe:
                corrected_mat = apply_clahe_to_rgb(corrected_mat, clahe_clip)
            elif op == "Saturation" and use_saturation and saturation_val != 1.0:
                hsv = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2HSV).astype(np.float32)
                hsv[..., 1] *= saturation_val
                hsv[..., 1] = np.clip(hsv[..., 1], 0, 255)
                corrected_mat = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
            elif op == "LUTs" and luts:
                for lut_array in luts:
                    corrected_mat = apply_3d_lut(corrected_mat, lut_array)
    #return RGB !
    #corrected_mat = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)

    return corrected_mat

def get_frame_preview(input_path, frame_number=None, use_clahe=False, min_avg_red=60, max_hue_shift=120, clahe_clip=2.0, use_saturation=False, saturation_val=1.0, use_brightness=False, brightness_val=0, contrast_val=1.0, luts=None, lut_only=False, ops_order=None):
    cap = cv2.VideoCapture(input_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    if frame_count > 0:
        if frame_number is None:
            # Default to the first frame (index 0) if no frame is specified
            frame_number = 0
        else:
            # OpenCV uses 0-based indexing for frames, but the UI uses 1-based indexing.
            # We need to subtract 1 from the frame_number provided by the UI.
            frame_number = max(0, min(int(frame_number) - 1, frame_count - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        
    ret, frame = cap.read()
    cap.release()
    
    if not ret or frame is None:
        return None, frame_count
        
    rgb_mat = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    corrected_mat = rgb_mat.copy()
    
    if ops_order is None:
        ops_order = ["Dive Color", "CLAHE", "Brightness", "Contrast", "Red Strength", "LUTs", "Hue Shift", "Saturation"]

    if lut_only:
        if luts:
            for lut_array in luts:
                corrected_mat = apply_3d_lut(corrected_mat, lut_array)
    else:
        filter_applied = False
        for op in ops_order:
            if op in ["Dive Color", "Red Strength", "Hue Shift"] and not filter_applied:
                corrected_mat = apply_filter(corrected_mat, get_filter_matrix(rgb_mat, use_clahe, min_avg_red, max_hue_shift))
                filter_applied = True
            elif op == "Brightness" and use_brightness and brightness_val != 0:
                corrected_mat = cv2.convertScaleAbs(corrected_mat, alpha=1.0, beta=brightness_val)
            elif op == "Contrast" and use_brightness and contrast_val != 1.0:
                corrected_mat = cv2.convertScaleAbs(corrected_mat, alpha=contrast_val, beta=0)
            elif op == "CLAHE" and use_clahe:
                corrected_mat = apply_clahe_to_rgb(corrected_mat, clahe_clip)
            elif op == "Saturation" and use_saturation and saturation_val != 1.0:
                hsv = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2HSV).astype(np.float32)
                hsv[..., 1] *= saturation_val
                hsv[..., 1] = np.clip(hsv[..., 1], 0, 255)
                corrected_mat = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
            elif op == "LUTs" and luts:
                for lut_array in luts:
                    corrected_mat = apply_3d_lut(corrected_mat, lut_array)
            
    preview = frame.copy()
    width = preview.shape[1] // 2
    height = preview.shape[0] // 2
    
    corrected_mat_bgr = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)
    preview[:, width:] = corrected_mat_bgr[:, width:]
    preview = cv2.resize(preview, (width, height))
    
    return cv2.imencode('.png', preview)[1].tobytes(), frame_count

def get_image_preview(input_path, use_clahe=False, min_avg_red=60, max_hue_shift=120, clahe_clip=2.0, use_saturation=False, saturation_val=1.0, use_brightness=False, brightness_val=0, contrast_val=1.0, luts=None, lut_only=False, ops_order=None):
    with Image.open(input_path) as image:
        if image.mode != "RGB":
            image = image.convert("RGB")
        mat = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    rgb_mat = cv2.cvtColor(mat, cv2.COLOR_BGR2RGB)
    corrected_mat = correct(rgb_mat, use_clahe, min_avg_red, max_hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)
    
    preview = mat.copy()
    width = preview.shape[1] // 2
    corrected_mat_bgr = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)
    preview[::, width:] = corrected_mat_bgr[::, width:]

    preview = cv2.resize(preview, (960, 540))

    return cv2.imencode('.png', preview)[1].tobytes()

def correct_image(input_path, output_path, use_clahe=False, min_avg_red=60, max_hue_shift=120, clahe_clip=2.0, use_saturation=False, saturation_val=1.0, use_brightness=False, brightness_val=0, contrast_val=1.0, luts=None, lut_only=False, ops_order=None):
    exif_data = None
    with Image.open(input_path) as image:
        exif_data = image.info.get("exif")
        if image.mode != "RGB":
            image = image.convert("RGB")
        mat = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    rgb_mat = cv2.cvtColor(mat, cv2.COLOR_BGR2RGB)
    corrected_mat = correct(rgb_mat, use_clahe, min_avg_red, max_hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)

    output_image = Image.fromarray(corrected_mat)
    save_kwargs = {}
    if exif_data:
        save_kwargs["exif"] = exif_data
    output_image.save(output_path, **save_kwargs)
    
    preview = mat.copy()
    width = preview.shape[1] // 2
    corrected_mat_bgr = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)
    preview[::, width:] = corrected_mat_bgr[::, width:]

    preview = cv2.resize(preview, (960, 540))

    return cv2.imencode('.png', preview)[1].tobytes()

def analyze_video(input_video_path, output_video_path, keep_sound=False, use_nvenc=False, use_clahe=False, min_avg_red=60, max_hue_shift=120, clahe_clip=2.0, use_saturation=False, saturation_val=1.0, use_brightness=False, brightness_val=0, contrast_val=1.0, start_frame=None, end_frame=None, luts=None, lut_only=False, ops_order=None, create_demo=False):
    cap = cv2.VideoCapture(input_video_path)
    fps = math.ceil(cap.get(cv2.CAP_PROP_FPS)) or 30
    total_video_frames = math.ceil(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    if start_frame is None:
        start_frame = 1
    if end_frame is None:
        end_frame = total_video_frames
        
    start_frame = max(1, min(start_frame, total_video_frames))
    end_frame = max(start_frame, min(end_frame, total_video_frames))
    
    # Calculate frames to analyze
    step = fps * SAMPLE_SECONDS
    sample_frames = [start_frame]
    
    first_aligned = ((start_frame + step - 1) // step) * step
    if first_aligned == start_frame:
        first_aligned += step
    for f in range(first_aligned, end_frame + 1, step):
        if f not in sample_frames:
            sample_frames.append(f)
            
    sample_frames.sort()
    
    filter_matrix_indexes = []
    filter_matrices = []
    
    segment_frame_count = end_frame - start_frame + 1
    
    print("Analyzing...")
    for idx, f in enumerate(sample_frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f - 1)
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
            
        mat = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Relative position within the processed segment
        rel_index = f - start_frame + 1
        filter_matrix_indexes.append(rel_index)
        filter_matrices.append(get_filter_matrix(mat, use_clahe, min_avg_red, max_hue_shift))
        
        yield rel_index, segment_frame_count
        
    cap.release()

    if not filter_matrices:
        filter_matrix_indexes = [1]
        filter_matrices = [get_filter_matrix(np.zeros((256, 256, 3), dtype=np.uint8), use_clahe, min_avg_red, max_hue_shift)]
        
    filter_matrices = np.array(filter_matrices)

    yield {
        "input_video_path": input_video_path,
        "output_video_path": output_video_path,
        "keep_sound": keep_sound,
        "use_nvenc": use_nvenc,
        "use_clahe": use_clahe,
        "clahe_clip": clahe_clip,
        "use_saturation": use_saturation,
        "saturation_val": saturation_val,
        "use_brightness": use_brightness,
        "brightness_val": brightness_val,
        "contrast_val": contrast_val,
        "fps": fps,
        "frame_count": segment_frame_count,
        "filters": filter_matrices,
        "filter_indices": filter_matrix_indexes,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "luts": luts,
        "lut_only": lut_only,
        "ops_order": ops_order,
        "create_demo": create_demo
    }

def precompute_filter_matrices(frame_count, filter_indices, filter_matrices):
    filter_matrix_size = len(filter_matrices[0])
    frame_numbers = np.arange(frame_count)
    interpolated_matrices = np.zeros((frame_count, filter_matrix_size))
    for x in range(filter_matrix_size):
        interpolated_matrices[:, x] = np.interp(frame_numbers, filter_indices, filter_matrices[:, x])
    return interpolated_matrices

def process_video(video_data, preview_state):
    cap = cv2.VideoCapture(video_data["input_video_path"])
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = video_data["fps"]
    frame_count = video_data["frame_count"]
    keep_sound = video_data.get("keep_sound", False)
    use_nvenc = video_data.get("use_nvenc", False)
    
    start_frame = video_data.get("start_frame", 1)
    end_frame = video_data.get("end_frame", frame_count)
    
    use_clahe = video_data.get("use_clahe", False)
    clahe_clip = video_data.get("clahe_clip", 2.0)
    use_saturation = video_data.get("use_saturation", False)
    saturation_val = video_data.get("saturation_val", 1.0)
    use_brightness = video_data.get("use_brightness", False)
    brightness_val = video_data.get("brightness_val", 0)
    contrast_val = video_data.get("contrast_val", 1.0)
    luts = video_data.get("luts", None)
    lut_only = video_data.get("lut_only", False)
    ops_order = video_data.get("ops_order", ["Dive Color", "CLAHE", "Brightness", "Contrast", "Red Strength", "LUTs", "Hue Shift", "Saturation"])
    create_demo = video_data.get("create_demo", False)

    print("Precomputing filter matrices...")
    interpolated_matrices = precompute_filter_matrices(
        frame_count, video_data["filter_indices"], np.array(video_data["filters"])
    )

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', f'{frame_width}x{frame_height}',
        '-pix_fmt', 'bgr24',
        '-r', str(fps),
        '-i', '-',
    ]

    if keep_sound:
        start_time_sec = (start_frame - 1) / fps
        duration_sec = frame_count / fps
        cmd.extend(['-ss', str(start_time_sec), '-t', str(duration_sec), '-i', video_data["input_video_path"]])

    if use_nvenc:
        cmd.extend([
            '-c:v', 'h264_nvenc',
            '-preset', 'p6',
            '-cq', '23',
            '-pix_fmt', 'yuv420p',
        ])
    else:
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
        ])

    if keep_sound:
        cmd.extend([
            '-c:a', 'aac',
            '-map', '0:v:0',
            '-map', '1:a:0?',
            '-shortest'
        ])

    cmd.append(video_data["output_video_path"])

    creationflags = 0
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NO_WINDOW

    ffmpeg_process = subprocess.Popen(
        cmd, 
        stdin=subprocess.PIPE, 
        stdout=subprocess.DEVNULL, 
        stderr=subprocess.DEVNULL,
        creationflags=creationflags
    )

    print("Processing...")
    count = 0
    
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame - 1)
    
    # Setup async writer thread
    write_queue = queue.Queue(maxsize=30)
    writer_error = []
    
    def writer_thread():
        try:
            while True:
                frame_bytes = write_queue.get()
                if frame_bytes is None:
                    break
                ffmpeg_process.stdin.write(frame_bytes)
        except Exception as e:
            writer_error.append(e)
            
    t = threading.Thread(target=writer_thread)
    t.daemon = True
    t.start()
    
    try:
        while cap.isOpened():
            if writer_error:
                break
                
            count += 1
            percent = 100 * count / frame_count
            
            if count % 50 == 0 or count == frame_count:
                print("{:.2f}%".format(percent), end="\r")
                
            ret, frame = cap.read()
            
            if not ret or count > frame_count:
                break

            rgb_mat = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            corrected_mat = rgb_mat.copy()
            
            if lut_only:
                if luts:
                    for lut_array in luts:
                        corrected_mat = apply_3d_lut(corrected_mat, lut_array)
            else:
                filter_applied = False
                for op in ops_order:
                    if op in ["Dive Color", "Red Strength", "Hue Shift"] and not filter_applied:
                        corrected_mat = apply_filter(corrected_mat, interpolated_matrices[count - 1])
                        filter_applied = True
                    elif op == "Brightness" and use_brightness and brightness_val != 0:
                        corrected_mat = cv2.convertScaleAbs(corrected_mat, alpha=1.0, beta=brightness_val)
                    elif op == "Contrast" and use_brightness and contrast_val != 1.0:
                        corrected_mat = cv2.convertScaleAbs(corrected_mat, alpha=contrast_val, beta=0)
                    elif op == "CLAHE" and use_clahe:
                        corrected_mat = apply_clahe_to_rgb(corrected_mat, clahe_clip)
                    elif op == "Saturation" and use_saturation and saturation_val != 1.0:
                        hsv = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2HSV).astype(np.float32)
                        hsv[..., 1] *= saturation_val
                        hsv[..., 1] = np.clip(hsv[..., 1], 0, 255)
                        corrected_mat = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
                    elif op == "LUTs" and luts:
                        for lut_array in luts:
                            corrected_mat = apply_3d_lut(corrected_mat, lut_array)
                    
            corrected_mat_bgr = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)
            
            if create_demo:
                width = frame.shape[1] // 2
                demo_mat = frame.copy()
                demo_mat[:, width:] = corrected_mat_bgr[:, width:]
                write_queue.put(demo_mat.tobytes())
            else:
                write_queue.put(corrected_mat_bgr.tobytes())

            if preview_state["show"]:
                preview = frame.copy()
                half_w = preview.shape[1] // 2
                preview[:, half_w:] = corrected_mat_bgr[:, half_w:]

                new_h = int(preview.shape[0] * 1080 / preview.shape[1])
                preview = cv2.resize(preview, (1080, new_h))

                yield percent, cv2.imencode('.png', preview)[1].tobytes(), count
            else:
                yield percent, None, count
    finally:
        cap.release()
        
        write_queue.put(None)
        t.join(timeout=5.0)
        
        if ffmpeg_process.stdin:
            ffmpeg_process.stdin.close()
        
        if count < frame_count * 0.99 or writer_error: 
            ffmpeg_process.kill()
        else:
            ffmpeg_process.wait()


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage")
        print("-"*20)
        print("For image:")
        print("$python correct.py image <source_image_path> <output_image_path>\n")
        print("-"*20)
        print("For video:")
        print("$python correct.py video <source_video_path> <output_video_path>\n")
        exit(0)

    if (sys.argv[1]) == "image":
        mat = cv2.imread(sys.argv[2])
        mat = cv2.cvtColor(mat, cv2.COLOR_BGR2RGB)
        
        corrected_mat = correct(mat, False, 60, 120, 2.0, False, 1.0, False, 0, 1.0)
        corrected_mat_bgr = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)

        cv2.imwrite(sys.argv[3], corrected_mat_bgr)
    
    else:

        for item in analyze_video(sys.argv[2], sys.argv[3], use_clahe=False, min_avg_red=60, max_hue_shift=120, clahe_clip=2.0, use_saturation=False, saturation_val=1.0, use_brightness=False, brightness_val=0, contrast_val=1.0):

            if type(item) == dict:
                video_data = item
            
        [x for x in process_video(video_data, {"show": False})]