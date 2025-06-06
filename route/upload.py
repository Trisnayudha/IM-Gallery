import subprocess

def mirror_to_contabo(local_path: str, subfolder: str, filename: str):
    """
    Jalankan rclone untuk menyalin file lokal ke Contabo.
    - local_path: path absolut ke file yang baru disimpan di disk.
    - subfolder: "original" atau "watermark/medium" atau "watermark/low".
    - filename: nama file, misal "abc123.jpg".
    """
    # Bentuk remote target: contabo:indonesiaminer/<subfolder>/<filename>
    remote_path = f"contabo:indonesiaminer/{subfolder}/{filename}"
    # Panggil rclone copyto
    subprocess.run(
        ["rclone", "copyto", local_path, remote_path],
        check=True
    )