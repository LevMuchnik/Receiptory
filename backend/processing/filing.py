import re


def generate_stored_filename(receipt_date: str | None, vendor_receipt_id: str | None, file_hash: str) -> str:
    date_part = receipt_date if receipt_date else "0000-00-00"
    id_part = vendor_receipt_id if vendor_receipt_id else "000000"
    hash_part = file_hash[:8]
    id_part = re.sub(r"[^a-zA-Z0-9\-_.]", "_", id_part)
    return f"{date_part}-{id_part}-{hash_part}.pdf"
