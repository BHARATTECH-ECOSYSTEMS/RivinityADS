import argparse
import asyncio
import csv
import sys
from pathlib import Path

# Add backend to path so we can import liteads
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from liteads.common.database import db
from liteads.models.geo import GeoLocation

async def process_file(filepath: str, clear: bool):
    print(f"Loading data from {filepath}...")
    
    rows_to_insert = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        # GeoNames format is tab separated
        reader = csv.reader(f, delimiter='\t')
        for i, row in enumerate(reader):
            if not row or len(row) < 10:
                continue
                
            # geonames standard columns:
            # 0: country code (e.g., IN)
            # 1: postal code
            # 2: place name
            # 3: admin1 (state)
            # 4: admin code1
            # 5: admin2
            # 6: admin code2
            # 7: admin3
            # 8: admin code3
            # 9: latitude
            # 10: longitude
            # 11: accuracy
            
            country = row[0].strip().upper()
            pincode = row[1].strip()
            city = row[2].strip().lower()
            state_name = row[3].strip().lower()
            state_code = row[4].strip().upper()
            
            lat_str = row[9].strip()
            lon_str = row[10].strip()
            
            try:
                lat = float(lat_str) if lat_str else None
                lon = float(lon_str) if lon_str else None
            except ValueError:
                lat = None
                lon = None
                
            if not country or not pincode:
                continue
                
            rows_to_insert.append(dict(
                country=country,
                pincode=pincode,
                city=city if city else None,
                state_name=state_name if state_name else None,
                state_code=state_code if state_code else None,
                lat=lat,
                lon=lon
            ))

    if not rows_to_insert:
        print("No valid rows found.")
        return

    print(f"Parsed {len(rows_to_insert)} rows. Inserting into database...")

    await db.init()

    async with db.engine.begin() as conn:
        if clear:
            print("Clearing existing geo_locations...")
            await conn.execute(delete(GeoLocation))
            
        # Postgres UPSERT using ON CONFLICT
        # We rely on the unique constraint (country, state_code, city, pincode)
        chunk_size = 5000
        total = len(rows_to_insert)
        
        for i in range(0, total, chunk_size):
            chunk = rows_to_insert[i:i + chunk_size]
            
            chunk_stmt = insert(GeoLocation).values(chunk)
            # Postgres upsert
            chunk_stmt = chunk_stmt.on_conflict_do_nothing(
                constraint="uq_geo_location_full"
            )
            await conn.execute(chunk_stmt)
            print(f"Inserted chunk {i//chunk_size + 1}/{(total + chunk_size - 1)//chunk_size}")

    print("Finished loading locations.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load GeoNames postal data into DB.")
    parser.add_argument("--file", required=True, help="Path to TSV file")
    parser.add_argument("--clear", action="store_true", help="Clear existing locations")
    args = parser.parse_args()
    
    asyncio.run(process_file(args.file, args.clear))
