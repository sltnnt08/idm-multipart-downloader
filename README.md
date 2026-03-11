# IDM Mass Multipart Downloader (CLI)

Queue many download links to IDM from a single config file, with safety checks for direct file URLs.

Downloader massal berbasis CLI untuk mengirim banyak link ke IDM dari satu file config, dengan validasi keamanan URL file.

---

## English — End-User Guide

### What this tool does

- Reads download inputs from `config.json`
- Resolves landing-page download buttons to direct links (supported hosts)
- Validates links before queueing (RAR check + HTML/landing-page rejection)
- Sends links to IDM queue and optionally starts queue automatically
- Supports resume state, dry-run, and existing-file handling (`ask/skip/overwrite`)

### Requirements

- Windows
- Python 3.11+
- Internet Download Manager (IDM)

Create and use a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `.venv` already exists in this repo, just activate it before running the app.

Install dependencies without activating the shell session:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Quick start (recommended)

0. Copy `config.template.json` to `config.json`.
1. Put all links into `download.txt` (one link per line, mixed domains are allowed).
2. (Optional) Set `input_file` in config if you want to use another text file.
3. (Optional) Set `id_url_template` if your input contains raw IDs.
4. Run safe check first:

```powershell
.\.venv\Scripts\python.exe main.py --config config.json --dry-run
```

5. If output looks good, run real queue:

```powershell
.\.venv\Scripts\python.exe main.py --config config.json
```

### Input modes (priority)

The app reads inputs in this order:

1. `paste_input`
2. `input_urls`
3. `input_file` / `download.txt`
4. `input_ids`
5. Multipart generator (`base_url` + `filename_pattern`)

If `paste_input` is filled, lower-priority modes are ignored.

### Important config options

- `download_path`: output folder
- `idm_path`: IDM executable path (empty = auto-detect)
- `queue_only`: use IDM queue mode (`/a`)
- `auto_start_queue`: start IDM queue after enqueue
- `worker_count`: number of concurrent workers for validation and IDM queue loading
- `resolve_download_button_links`: parse direct link from `DOWNLOAD` button pages
- `selenium_fallback_enabled`: use Selenium click fallback when HTML parsing fails
- `selenium_headless`: run Selenium with hidden browser
- `require_rar_extension`: allow queue only if target looks like a RAR file
- `reject_html_content`: reject HTML landing/ad pages
- `existing_file_action`: `ask` / `skip` / `overwrite`
- `resume_mode`: skip previously queued URLs from resume state
- `validate_resume_with_idm`: sync resume state with IDM local state

### Existing file behavior (new)

When destination file already exists in `download_path`:

- `existing_file_action = ask` → prompt user
- `existing_file_action = skip` → always skip existing files
- `existing_file_action = overwrite` → remove existing file, queue again

Interactive prompt choices:

- `s` = skip once
- `o` = overwrite once
- `k` = skip all
- `a` = overwrite all

### Useful commands

```powershell
.\.venv\Scripts\python.exe main.py --config config.json
.\.venv\Scripts\python.exe main.py --config config.json --dry-run
.\.venv\Scripts\python.exe main.py --config config.json --no-resume
```

### Troubleshooting

- `download.txt` is not read → ensure `input_file` points to the right file or keep the default `./download.txt` next to `config.json`.
- `IDM executable not found` → set `idm_path` correctly or leave empty for auto-detect.
- Queue empty after run → check `resume_mode`, `existing_file_action`, and `log.txt` diagnostics.
- URL requires button click → keep `resolve_download_button_links=true`; optionally enable `selenium_fallback_enabled=true`.
- Risk of ad/landing pages → keep `require_rar_extension=true` and `reject_html_content=true`.

---

## Indonesia — Panduan End-User

### Fungsi tool ini

- Membaca input download dari `config.json`
- Resolve halaman landing tombol download jadi direct link (untuk host yang didukung)
- Validasi link sebelum masuk queue (cek RAR + tolak HTML/landing page)
- Mengirim link ke antrean IDM dan bisa auto-start queue
- Mendukung resume state, dry-run, dan aturan file existing (`ask/skip/overwrite`)

### Prasyarat

- Windows
- Python 3.11+
- Internet Download Manager (IDM)

Buat dan gunakan virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Kalau `.venv` di repo ini sudah ada, cukup aktifkan dulu sebelum menjalankan program.

Install dependency tanpa aktivasi shell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Quick start (disarankan)

0. Copy `config.template.json` menjadi `config.json`.
1. Simpan semua link ke `download.txt` (1 baris 1 link, domain bisa campur).
2. (Opsional) Ubah `input_file` jika ingin pakai nama file lain.
3. (Opsional) Isi `id_url_template` jika input berisi ID mentah.
4. Jalankan cek aman dulu:

```powershell
.\.venv\Scripts\python.exe main.py --config config.json --dry-run
```

5. Kalau output sesuai, jalankan queue real:

```powershell
.\.venv\Scripts\python.exe main.py --config config.json
```

### Mode input (prioritas)

Sistem membaca input dengan urutan:

1. `paste_input`
2. `input_urls`
3. `input_file` / `download.txt`
4. `input_ids`
5. Generator multipart (`base_url` + `filename_pattern`)

Kalau `paste_input` terisi, mode di bawahnya diabaikan.

### Opsi config penting

- `download_path`: folder hasil download
- `idm_path`: path executable IDM (kosong = auto-detect)
- `queue_only`: kirim ke antrean dulu (`/a`)
- `auto_start_queue`: jalankan antrean IDM setelah enqueue
- `worker_count`: jumlah worker paralel untuk validasi link dan pengiriman ke antrean IDM
- `resolve_download_button_links`: parsing direct link dari halaman tombol `DOWNLOAD`
- `selenium_fallback_enabled`: fallback klik Selenium jika parsing HTML gagal
- `selenium_headless`: Selenium jalan tanpa UI browser
- `require_rar_extension`: hanya izinkan target yang terindikasi file RAR
- `reject_html_content`: tolak respons HTML/landing page
- `existing_file_action`: `ask` / `skip` / `overwrite`
- `resume_mode`: skip URL yang sudah pernah di-queue
- `validate_resume_with_idm`: sinkronkan resume state dengan data lokal IDM

### Perilaku jika file sudah ada (fitur baru)

Jika file tujuan sudah ada di `download_path`:

- `existing_file_action = ask` → user diprompt
- `existing_file_action = skip` → otomatis lewati
- `existing_file_action = overwrite` → hapus file lama, queue ulang

Pilihan saat prompt:

- `s` = skip sekali
- `o` = overwrite sekali
- `k` = skip semua
- `a` = overwrite semua

### Command yang sering dipakai

```powershell
.\.venv\Scripts\python.exe main.py --config config.json
.\.venv\Scripts\python.exe main.py --config config.json --dry-run
.\.venv\Scripts\python.exe main.py --config config.json --no-resume
```

### Troubleshooting

- `download.txt` tidak terbaca → pastikan `input_file` mengarah ke file yang benar atau biarkan default `./download.txt` di folder yang sama dengan `config.json`.
- `IDM executable not found` → isi `idm_path` dengan benar atau kosongkan untuk auto-detect.
- Queue kosong setelah run → cek `resume_mode`, `existing_file_action`, dan diagnostik di `log.txt`.
- URL perlu klik tombol dulu → pastikan `resolve_download_button_links=true`; opsional aktifkan `selenium_fallback_enabled=true`.
- Risiko link iklan/landing → biarkan `require_rar_extension=true` dan `reject_html_content=true`.

---

## Project structure

- `main.py` — app orchestration, queue flow, summary
- `config_loader.py` — config parsing + validation
- `download_link_resolver.py` — landing-page/direct-link resolver (+ Selenium fallback)
- `validator.py` — URL safety validation
- `idm_controller.py` — IDM command integration
- `download.txt` — default source file for mixed direct links
