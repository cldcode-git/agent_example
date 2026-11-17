"""3A Superstore veri setini Kaggle'dan indirip data/raw klasörüne koyar.

Kullanım:
    python scripts/download_data.py

Herkese açık veri seti olduğu için Kaggle hesabı gerekmez. İndirme ~3 GB'tır.
Sadece Türkçe orijinal dosyalar kullanılır (*_ENG.csv çeviri dosyaları alınmaz).
"""

import os
import shutil
import sys
from pathlib import Path

import kagglehub

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from superstore_agents.config import PROJECT_ROOT  # noqa: E402

DATASET = "cemeraan/3a-superstore"
FILES = ["Branches.csv", "Categories.csv", "Customers.csv", "Orders.csv", "Order_Details.csv"]


def main() -> None:
    target = PROJECT_ROOT / "data" / "raw"
    target.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(kagglehub.dataset_download(DATASET))

    for name in FILES:
        src, dst = cache_dir / name, target / name
        if dst.exists():
            dst.unlink()
        try:
            os.link(src, dst)        # aynı diskteyse kopyalamadan "sabit bağlantı" (yer kaplamaz)
        except OSError:
            shutil.copy(src, dst)
        print(f"  {name:<20} {dst.stat().st_size / 1e6:>8,.0f} MB")
    print(f"-> {target}")


if __name__ == "__main__":
    main()
