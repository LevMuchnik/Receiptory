from pydantic import BaseModel, Field
from typing import Any


# === Auth ===

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    message: str
    username: str


class AuthMeResponse(BaseModel):
    username: str


# === Categories ===

class CategoryCreate(BaseModel):
    name: str
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    display_order: int | None = None


class ReorderItem(BaseModel):
    id: int
    display_order: int


class ReorderRequest(BaseModel):
    order: list[ReorderItem]


class CategoryResponse(BaseModel):
    id: int
    name: str
    description: str | None
    is_system: bool
    is_deleted: bool
    display_order: int | None
    created_at: str
    updated_at: str


# === Documents ===

class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None


class AdditionalField(BaseModel):
    key: str
    value: str


class DocumentResponse(BaseModel):
    id: int
    document_type: str | None
    original_filename: str
    stored_filename: str | None
    file_hash: str
    file_size_bytes: int
    page_count: int | None

    submission_date: str
    submission_channel: str
    sender_identifier: str | None

    receipt_date: str | None
    document_title: str | None
    vendor_name: str | None
    vendor_tax_id: str | None
    vendor_receipt_id: str | None
    client_name: str | None
    client_tax_id: str | None
    description: str | None
    line_items: list[LineItem] | None = None
    subtotal: float | None
    tax_amount: float | None
    total_amount: float | None
    currency: str | None
    converted_amount: float | None
    conversion_rate: float | None
    payment_method: str | None
    payment_identifier: str | None
    language: str | None
    additional_fields: list[AdditionalField] | None = None
    raw_extracted_text: str | None

    category_id: int | None
    category_name: str | None = None
    status: str

    extraction_confidence: float | None
    processing_model: str | None
    processing_tokens_in: int | None
    processing_tokens_out: int | None
    processing_cost_usd: float | None
    processing_date: str | None
    processing_attempts: int
    processing_error: str | None

    manually_edited: bool
    is_deleted: bool
    edit_history: list[dict] | None = None
    user_notes: str | None

    last_exported_date: str | None
    created_at: str
    updated_at: str


class DocumentUpdate(BaseModel):
    receipt_date: str | None = None
    document_title: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_receipt_id: str | None = None
    client_name: str | None = None
    client_tax_id: str | None = None
    description: str | None = None
    total_amount: float | None = None
    currency: str | None = None
    category_id: int | None = None
    document_type: str | None = None
    status: str | None = None
    user_notes: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class DuplicateGroup(BaseModel):
    receipt_date: str | None
    vendor_receipt_id: str | None
    documents: list[DocumentResponse]


# === Export ===

class ExportRequest(BaseModel):
    preset: str | None = None  # since_last_export, month, date_range, full_year
    date_from: str | None = None
    date_to: str | None = None
    month: str | None = None  # YYYY-MM
    year: int | None = None
    status: str | None = None
    category_id: int | None = None
    document_type: str | None = None
    document_ids: list[int] | None = None


# === Settings ===

class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


# === Stats ===

class DashboardStats(BaseModel):
    processed_this_month: int
    total_expenses_by_category: list[dict]
    pending_review_count: int
    recent_activity: list[dict]


class ProcessingCosts(BaseModel):
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    by_model: list[dict]


# === Queue ===

class QueueStatus(BaseModel):
    pending: int
    processing: int
    recent_completed: int
    recent_failed: int
    current_document: dict | None


# === Backup ===

class BackupResponse(BaseModel):
    id: int
    started_at: str
    completed_at: str | None
    status: str
    size_bytes: int | None
    destination: str | None
    error: str | None
    backup_type: str


# === Batch operations ===

class BatchReprocessRequest(BaseModel):
    document_ids: list[int] | None = None
    status: str | None = None
    category_id: int | None = None
