# PNGTrim
PNGTrim is a small GUI tool that shrinks PNG files to a target size. 

For each file it tries progressively smaller scales and fewer colors (using libimagequant quantization when your Pillow build includes it) until the result fits the limit. Transparency is preserved.

Features:

- process a whole folder, including subfolders
- configurable size limit
- optional metadata removal (EXIF, text chunks, ICC profile, DPI)
- backups of the originals (.png.bak)


<details>
<summary>a?</summary>
(ai slob yes)
</details>
