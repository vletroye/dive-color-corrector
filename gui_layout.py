import os
import PySimpleGUI as sg
import subprocess
from logo.logo import LOGO
from lut_manager import get_luts_list

DEFAULT_OPS = ["Dive Color", "CLAHE", "Brightness", "Contrast", "Red Strength", "LUTs", "Hue Shift", "Saturation"]

def has_nvidia_gpu():
    """Détecte la présence d'un GPU NVIDIA sur le système."""
    try:
        if os.name == 'nt':
            creationflags = 0x08000000  # subprocess.CREATE_NO_WINDOW
            output = subprocess.check_output(['wmic', 'path', 'win32_VideoController', 'get', 'name'], stderr=subprocess.DEVNULL, creationflags=creationflags).decode()
            return 'NVIDIA' in output.upper()
        else:
            output = subprocess.check_output(['lspci'], stderr=subprocess.DEVNULL).decode()
            return 'NVIDIA' in output.upper()
    except Exception:
        return False

def setup_theme():
    """Configure le thème visuel sombre moderne pour l'application."""
    my_theme = {
        'BACKGROUND': '#1e1e1e',
        'TEXT': '#e0e0e0',
        'INPUT': '#2d2d2d',
        'TEXT_INPUT': '#ffffff',
        'SCROLL': '#2d2d2d',
        'BUTTON': ('#ffffff', '#3d3d3d'),
        'PROGRESS': ('#007acc', '#2d2d2d'),
        'BORDER': 1,
        'SLIDER_DEPTH': 0,
        'PROGRESS_DEPTH': 0
    }
    sg.theme_add_new('ModernDark', my_theme)
    sg.theme('ModernDark')
    sg.set_options(font=("Segoe UI", 11), slider_relief=sg.RELIEF_FLAT)
    sg.set_global_icon(LOGO)

def get_btn_style(img_name, default_text, default_colors=None):
    """Retourne une image de bouton si disponible dans /assets, sinon un style texte."""
    path = os.path.join("assets", f"{img_name}.png")
    if os.path.exists(path):
        return {"image_filename": path, "button_color": ('#1e1e1e', '#1e1e1e'), "border_width": 0, "button_text": ""}

    kwargs = {"button_text": default_text}
    if default_colors:
        kwargs["button_color"] = default_colors
    return kwargs

def show_help_window():
    """Affiche la fenêtre modale du guide utilisateur."""
    help_layout = [
        [sg.Text("User Guide - Dive Color Corrector", font=('Segoe UI', 16, 'bold'), text_color='#007acc')],
        [sg.Text("Step 1: Create a Project & Add Media", font=('Segoe UI', 12, 'bold'))],
        [sg.Text("  • Click 'New' to start a fresh project or 'Load' to open an existing one.")],
        [sg.Text("  • Use 'Select photos and videos' to add files to the list.")],
        [sg.Text("  • Select a file in the list to preview it.")],
        [sg.Text("")],
        [sg.Text("Step 2: Video Sequencing (Cutting)", font=('Segoe UI', 12, 'bold'))],
        [sg.Text("  • Check 'Show Preview & Cut' to enable the timeline.")],
        [sg.Text("  • Use the slider or timeline to navigate through the video.")],
        [sg.Text("  • Click 'Add Mark' to create a sequence. A green marker indicates the start, red indicates the end.")],
        [sg.Text("  • Drag markers on the timeline to adjust sequence boundaries.")],
        [sg.Text("  • Only the defined sequences will be processed and exported.")],
        [sg.Text("")],
        [sg.Text("Step 3: Adjust Processing Settings", font=('Segoe UI', 12, 'bold'))],
        [sg.Text("  • Red Strength: Restores red colors lost underwater. Increase for deeper dives.")],
        [sg.Text("  • Hue Shift: Adjusts the color rotation to fix green/blue tints.")],
        [sg.Text("  • CLAHE: Enhances local contrast, great for revealing details in dark areas.")],
        [sg.Text("  • Brightness/Contrast/Saturation: Standard adjustments for final touches.")],
        [sg.Text("  • LUTs: Apply color grading presets. Import custom .cube files if needed.")],
        [sg.Text("  • Reorder operations in the list to change how effects are applied.")],
        [sg.Text("")],
        [sg.Text("Step 4: Export", font=('Segoe UI', 12, 'bold'))],
        [sg.Text("  • Choose an 'Output folder' and set a prefix/suffix for the new files.")],
        [sg.Text("  • Click 'Start' to process all files and sequences in the list.")],
        [sg.Button("Close", pad=(0, 10), button_color=('#ffffff', '#007acc'))]
    ]

    win = sg.Window("User Guide", help_layout, modal=True, keep_on_top=True, finalize=True)
    while True:
        event, _ = win.read()
        if event in (sg.WIN_CLOSED, "Close"):
            break
    win.close()

def build_main_layout(has_nvidia):
    """Construit la structure de mise en page principale PySimpleGUI."""
    frame_files = [
        [
            sg.Input(visible=False, enable_events=True, key='__INPUT_FILES__'),
            sg.FilesBrowse(button_text="Select photos and videos", target='__INPUT_FILES__', key='__FILES_BROWSE_BTN__', button_color=('#ffffff', '#007acc')),
            sg.Push(),
            sg.Button(enable_events=True, pad=(5, 0), disabled=False, key="__NEW_PROJECT__", **get_btn_style("new", "New")),
            sg.Button(enable_events=True, pad=(5, 0), disabled=False, key="__LOAD_PROJECT__", **get_btn_style("load", "Load")),
            sg.Button(enable_events=True, pad=(5, 0), disabled=False, key="__SAVE_PROJECT__", **get_btn_style("save", "Save")),
            sg.Button("Help", enable_events=True, pad=(5, 0), key="__HELP_BTN__", button_color=('#ffffff', '#007acc'))
        ],
        [
            sg.Listbox(values=[], enable_events=True, size=(55, 8), key="__INPUT_FILE_LIST__", background_color='#252525',
                       right_click_menu=['', ['Process', '---', 'Open File', 'Open in Explorer', '---', 'Remove from list']])
        ],
        [
            sg.Text("Output folder", size=(12, 1)),
            sg.InputText(default_text="./", size=(31, 1), enable_events=True, readonly=True, text_color='#aaaaaa', background_color='#1e1e1e', key="__OUTPUT_FOLDER__"),
            sg.FolderBrowse(key="__OUTPUT_BROWSE__", button_color=('#ffffff', '#007acc'))
        ],
        [
            sg.Text(text="Output pre/suf-fix", size=(15, 1)),
            sg.InputText(default_text="corrected", size=(15, 1), key="__OUTPUT_PREFIX__"),
            sg.Checkbox("Suffix", default=True, key="__IS_SUFFIX__")
        ]
    ]

    frame_settings = [
        [
            sg.Checkbox("Keep sound (Videos only)", default=True, key="__KEEP_SOUND__"),
            sg.Push(),
            sg.Checkbox("Use NVENC", default=has_nvidia, key="__USE_NVENC__", tooltip="Requires NVIDIA GPU")
        ],
        [
            sg.Checkbox("Create Demo (Split screen)", default=False, key="__CREATE_DEMO__", tooltip="Export video with left half original and right half corrected")
        ],
        [sg.HSeparator(pad=(0, 10))],
        [
            sg.Checkbox("Use Brightness / Contrast", default=False, key="__USE_BRIGHTNESS__", enable_events=True, font=("Segoe UI", 11, "bold"))
        ],
        [
            sg.Text("Brightness", size=(12, 1), text_color='gray', key="__BRIGHTNESS_TEXT__"),
            sg.Slider(range=(-100, 100), default_value=0, orientation='h', size=(32, 15), key="__BRIGHTNESS__", enable_events=True, disable_number_display=False, disabled=True)
        ],
        [
            sg.Text("Contrast", size=(12, 1), text_color='gray', key="__CONTRAST_TEXT__"),
            sg.Slider(range=(0.5, 3.0), default_value=1.0, resolution=0.1, orientation='h', size=(32, 15), key="__CONTRAST__", enable_events=True, disable_number_display=False, disabled=True)
        ],
        [sg.HSeparator(pad=(0, 10))],
        [
            sg.Checkbox("Use CLAHE (Advanced contrast)", default=False, key="__USE_CLAHE__", enable_events=True, font=("Segoe UI", 11, "bold"), tooltip="Better for dark underwater scenes")
        ],
        [
            sg.Text("CLAHE Limit", size=(12, 1), text_color='gray', key="__CLAHE_CLIP_TEXT__"),
            sg.Slider(range=(0.1, 10.0), default_value=2.0, resolution=0.1, orientation='h', size=(32, 15), key="__CLAHE_CLIP__", enable_events=True, disable_number_display=False, disabled=True)
        ],
        [sg.HSeparator(pad=(0, 10))],
        [
            sg.Checkbox("Use Saturation", default=False, key="__USE_SATURATION__", enable_events=True, font=("Segoe UI", 11, "bold"))
        ],
        [
            sg.Text("Saturation", size=(12, 1), text_color='gray', key="__SATURATION_TEXT__"),
            sg.Slider(range=(0.0, 3.0), default_value=1.0, resolution=0.1, orientation='h', size=(32, 15), key="__SATURATION__", enable_events=True, disable_number_display=False, disabled=True)
        ],
        [sg.HSeparator(pad=(0, 10))],
        [
            sg.Text("Red Strength", size=(12, 1), font=("Segoe UI", 11, "bold")),
            sg.Slider(range=(10, 150), default_value=60, orientation='h', size=(32, 15), key="__RED_STRENGTH__", enable_events=True, disable_number_display=False)
        ],
        [
            sg.Text("Hue Shift", size=(12, 1), font=("Segoe UI", 11, "bold")),
            sg.Slider(range=(0, 180), default_value=120, orientation='h', size=(32, 15), key="__HUE_SHIFT__", enable_events=True, disable_number_display=False)
        ]
    ]

    left_column = [
        [sg.Frame("Media & Export [[empty]]", frame_files, pad=(5, 5), expand_x=True, font=("Segoe UI", 12, "bold"), title_color="#007acc", key="__MEDIA_FRAME__")],
        [sg.Frame("Processing Settings", frame_settings, pad=(5, 5), expand_x=True, font=("Segoe UI", 12, "bold"), title_color="#007acc")],
        [
            sg.Checkbox("Show Preview & Cut", default=False, key="__SHOW_PREVIEW__", enable_events=True, font=("Segoe UI", 12, "bold"), text_color="#007acc")
        ],
        [
            sg.Button(enable_events=True, pad=(5, 10), key="__RESET_SETTINGS__", **get_btn_style("reset", "Reset Settings")),
            sg.Push(),
            sg.Button(enable_events=True, pad=(5, 10), font=("Segoe UI", 11, "bold"), key="__CORRECT__", **get_btn_style("start", "Start", ('#ffffff', '#28a745'))),
            sg.Button(enable_events=True, pad=(5, 10), key="__OPEN_OUTPUT__", **get_btn_style("open", "Explore", ('#ffffff', '#007acc'))),
            sg.Button(enable_events=True, pad=(5, 10), disabled=True, key="__CANCEL__", **get_btn_style("cancel", "Cancel", ('#ffffff', '#dc3545')))
        ],
        [
            sg.Text(text="", size=(50, 1), text_color='#ffcc00', key="__STATUS__")
        ]
    ]

    info = [
        [
            sg.Text("DCC: Dive Color Corrector", font=('Segoe UI', 30, 'bold'))
        ],
        [
            sg.Text("Fork by"),
            sg.Text("@vletroye", pad=(1, 0), font=('Segoe UI', 11, 'bold'), text_color='#007acc'),
        ],
        [
            sg.Text("", pad=(5, 2))
        ],
        [
            sg.Text("Settings Guide", font=('Segoe UI', 16, 'bold'))
        ],
        [
            sg.Text("• Keep sound:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Retains original audio track in output videos.")
        ],
        [
            sg.Text("• Use NVENC:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Uses NVIDIA GPU for much faster video encoding.")
        ],
        [
            sg.Text("• Bright. / Contrast:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Standard linear exposure and contrast adjustments.")
        ],
        [
            sg.Text("• CLAHE:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Adaptive contrast. Reveals details in dark underwater shadows.")
        ],
        [
            sg.Text("• Saturation:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Boosts or reduces the overall color vibrancy.")
        ],
        [
            sg.Text("• Red Strength:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Target level for red channel restoration (counters blue water).")
        ],
        [
            sg.Text("• Hue Shift:", font=('Segoe UI', 12, 'bold'), size=(18, 1)), sg.Text("Maximum color rotation limit applied to restore reds.")
        ],
        [
            sg.Text("", pad=(10, 5))
        ],
        [
            sg.Text("Credits", font=('Segoe UI', 14, 'bold'), text_color='#aaaaaa')
        ],
        [
            sg.Text("This fork is based on and inspired by the original work and repository at:", text_color='#aaaaaa')
        ],
        [
            sg.Text("bornfree.github.io/dive-color-corrector", text_color='#007acc', font=('Segoe UI', 11, 'underline'), enable_events=True, key='__ORIGINAL_LINK__', pad=(15, 0))
        ],
        [
            sg.Text("originally originating from the algorithm at:", text_color='#aaaaaa')
        ],
        [
            sg.Text("github.com/nikolajbech/underwater-image-color-correction", text_color='#007acc', font=('Segoe UI', 11, 'underline'), enable_events=True, key='__ALGO_LINK__', pad=(15, 0))
        ],
    ]

    viewer = [
        [
            sg.Frame("", layout=info, key="__INFO__", pad=(0, 0), border_width=0, expand_x=True),
            sg.Column([
                [sg.Canvas(key="__PREVIEW_CANVAS__", background_color='#1e1e1e', expand_x=True, expand_y=True)]
            ], key="__PREVIEW_FRAME__", pad=(0, 0), expand_x=True, expand_y=True, visible=False)
        ],
        [
            sg.Column([
                [sg.Button("< Rewind", key="__GEN_REW__", disabled=True),
                 sg.Button("Stop ||", key="__GEN_STOP__", disabled=True),
                 sg.Button("Play >", key="__GEN_PLAY__", disabled=True)]
            ], key="__GENERAL_PLAY_CONTAINER__", visible=False, pad=(0, 2), element_justification='center', expand_x=True)
        ],
        [
            sg.Column([
                [sg.Slider(range=(1, 100), default_value=1, orientation='h', size=(10, 15), enable_events=True, key="__FRAME_SLIDER__", visible=False, disable_number_display=True, pad=(0, 2), expand_x=True)],
                [sg.Graph(canvas_size=(1070, 30), graph_bottom_left=(1, 0), graph_top_right=(100, 30), enable_events=True, drag_submits=True, key="__TIMELINE__", background_color='#151515', pad=(5, 0), expand_x=True)],
                [
                    sg.Button(key="__ADD_MARK__", **get_btn_style("add_mark", "Add Mark", ('#ffffff', '#28a745'))),
                    sg.Button(key="__DEL_MARK__", disabled=True, **get_btn_style("delete", "Delete", ('#ffffff', '#dc3545'))),
                    sg.Button("<<", key="__MARKER_FAST_REW__", disabled=True),
                    sg.Button("<", key="__MARKER_REW__", disabled=True),
                    sg.Button(">", key="__MARKER_FWD__", disabled=True),
                    sg.Button(">>", key="__MARKER_FAST_FWD__", disabled=True),
                    sg.Button(key="__UNDO__", disabled=True, **get_btn_style("undo", "Undo")),
                    sg.Button(key="__REDO__", disabled=True, **get_btn_style("redo", "Redo")),
                    sg.Button(key="__SAVE_FRAME__", disabled=True, **get_btn_style("save_frame", "Save Frame", ('#ffffff', '#007acc'))),
                    sg.Button(key="__FULL_PREVIEW__", disabled=True, **get_btn_style("full_preview", "Full Preview", ('#ffffff', '#007acc'))),
                    sg.Button("LUT Only", key="__LUT_ONLY_BTN__", disabled=True, button_color=('#ffffff', '#007acc'))
                ]
            ], key="__TIMELINE_CONTAINER__", visible=False, pad=(0, 0), element_justification='center', expand_x=True)
        ],
        [
            sg.Column([
                [
                    sg.Column([
                        [sg.Text("LUT available:", font=('Segoe UI', 9))],
                        [sg.Listbox(values=get_luts_list(), size=(22, 6), key="__LUT_LIST__", enable_events=True, select_mode=sg.LISTBOX_SELECT_MODE_SINGLE)],
                    ], element_justification='left', expand_y=True),
                    sg.Column([
                        [sg.Button("=>", key="__LUT_ADD_APPLIED__", size=(4, 1), disabled=True)],
                        [sg.Button("<=", key="__LUT_REM_APPLIED__", size=(4, 1), disabled=True)],
                        [sg.Button("Import LUT", key="__ADD_LUT__", button_color=('#ffffff', '#007acc'))],
                    ], element_justification='center', vertical_alignment='center'),
                    sg.Column([
                        [sg.Text("LUT applied:", font=('Segoe UI', 9))],
                        [sg.Listbox(values=[], size=(22, 6), key="__APPLIED_LUT_LIST__", enable_events=True, select_mode=sg.LISTBOX_SELECT_MODE_SINGLE, expand_y=True)],
                    ], expand_y=True),
                    sg.Column([
                        [sg.Button("▲", key="__APPLIED_LUT_UP__", size=(2, 1), disabled=True)],
                        [sg.Button("▼", key="__APPLIED_LUT_DOWN__", size=(2, 1), disabled=True)],
                        [sg.Text("", size=(2, 1), pad=(0, 4))],
                    ], element_justification='center', vertical_alignment='center'),
                    sg.Push(),
                    sg.Column([
                        [sg.Button("▲", key="__OP_UP__", disabled=True, size=(2, 1))],
                        [sg.Button("▼", key="__OP_DOWN__", disabled=True, size=(2, 1))],
                        [sg.Text("", size=(2, 1), pad=(0, 4))],
                    ], element_justification='center', vertical_alignment='center'),
                    sg.Column([
                        [sg.Text("Treatments:", font=('Segoe UI', 9))],
                        [sg.Listbox(values=DEFAULT_OPS, size=(20, 4), key="__OPS_LIST__", enable_events=True, select_mode=sg.LISTBOX_SELECT_MODE_SINGLE, expand_y=True, no_scrollbar=True)],
                    ], element_justification='left', expand_y=True)
                ]
            ], key="__LUTS_CONTAINER__", visible=False, pad=(0, 2), element_justification='left', expand_x=True)
        ]
    ]

    return [
        [
            sg.Column(left_column, vertical_alignment='top', pad=(10, 10)),
            sg.VSeparator(pad=(10, 0)),
            sg.Column(viewer, vertical_alignment='top', element_justification='l', pad=(10, 10), expand_x=True, expand_y=True, key='__VIEWER_COL__')
        ]
    ]
