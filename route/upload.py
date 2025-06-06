import os
import subprocess

def mirror_to_contabo(local_path: str, subfolder: str, filename: str):
    """
    Jalankan rclone untuk menyalin file lokal ke Contabo.
    - local_path: path absolut ke file yang baru disimpan di disk.
    - subfolder: "original", "watermark/medium", atau "watermark/low", termasuk folder dinamis.
    - filename: nama file, misal "abc123.jpg".
    """
    # Nama bucket di Contabo (diambil dari environment, default "indonesiaminer" jika tidak diset)
    bucket = os.getenv("CONTABO_BUCKET", "indonesiaminer")
    # Prefix utama di dalam bucket
    base_folder = "gallery"
    # Bentuk full key: <bucket>/gallery/<subfolder>/<filename>
    key = f"{bucket}/{base_folder}/{subfolder}/{filename}"
    # Panggil rclone copyto dengan syntax: contabo:<bucket>/<prefix>/<filename>
    subprocess.run(
        ["rclone", "copyto", local_path, f"contabo:{key}"],
        check=True
    )