## Dive and underwater image and video color correction

**Sample images**

![Example](./examples/example.jpg)

**Sample video**

[![Video](https://img.youtube.com/vi/NEpl41-LMBs/0.jpg)](https://www.youtube.com/watch?v=NEpl41-LMBs)

### Setup
```
$ pip install -r requirements.txt
```

### For images
```
$ python correct.py image /my/raw.png /my/corrected.png
```

### For videos
```
$ python correct.py video /my/raw.mp4 /my/corrected.mp4
```

### Script Parameters (`correct.py`)

The underlying functions and processing scripts support fine-tuned arguments for controlling the color correction process:

* --clahe: Enables the Contrast Limited Adaptive Histogram Equalization (CLAHE) algorithm to enhance shadows and contrast in dark underwater scenes.
* --red <int>: Sets the red strength threshold (min_avg_red, default: 60).
* --hue <int>: Sets the maximum hue shift limit (max_hue_shift, default: 120).
* --clahe-clip <float>: Sets the intensity of the CLAHE effect (default: 2.0).
* --saturation <float>: Sets the color saturation multiplier (default: 1.0).
* --brightness <int>: Sets the brightness offset from -100 to 100 (default: 0).
* --contrast <float>: Sets the contrast multiplier (default: 1.0).

#### Examples with Custom Parameters

**For an image with CLAHE and a custom red threshold:**
```
python correct.py image /my/raw.png /my/corrected.png --clahe --red 70 --clahe-clip 1.5
```

**For a video with CLAHE, saturation, and brightness/contrast adjustment:**
```
python correct.py video /my/raw.mp4 /my/corrected.mp4 --clahe --saturation 1.2 --brightness 10 --contrast 1.1
```

## GUI
You can either download the [desktop softwares](https://github.com/vletroye/dive-color-corrector) or build one yourself.

![GUI](./examples/gui.jpg)

### Building the GUI
Uncomment the libraries needed for GUI in `requirements.txt` and re-run `pip install`.

MacOS (via Py2App)
```
$ py2applet --make-setup dcc.py
$ python setup.py py2app
```

Windows (via PyInstaller)
```
$ python -m PyInstaller -n "Dive Color Corrector" -F -w -i .\logo\logo.ico dcc.py
```

Linux (via PyInstaller)
```
$ pyinstaller -n "Dive Color Corrector" -F -w -i ./logo/logo.png dcc.py
```

Final builds will be available in 'dist' folder


### Inspiration
This fork is based on and inspired by the original work and repository at [https://bornfree.github.io/dive-color-corrector/](https://bornfree.github.io/dive-color-corrector/) (originally originating from the algorithm at [https://github.com/nikolajbech/underwater-image-color-correction](https://github.com/nikolajbech/underwater-image-color-correction)).
