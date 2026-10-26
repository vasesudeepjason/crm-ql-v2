"""
Tax invoice PDF. Layout carried over from the previous CRM's ReportLab template; figures
come from the Invoice model in paise (no float arithmetic here) and the tax summary is
grouped by GST rate so a mixed-rate invoice prints correctly.
"""
from __future__ import annotations

import os
from html import escape
from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .gst import STATE_CODES, amount_to_words

REG, BOLD = "Helvetica", "Helvetica-Bold"


def e(v) -> str:
    return escape(str(v or "")).replace("\n", "<br/>")


def money(paise) -> str:
    """1,23,456.78 with Indian grouping, no symbol (Helvetica has no ₹ glyph)."""
    paise = int(paise or 0)
    neg = paise < 0
    r, p = divmod(abs(paise), 100)
    s = str(r)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + f"{s}.{p:02d}"


def _pct(d) -> str:
    return f"{float(d):g}%"


def build_invoice_pdf(inv, cfg, tenant) -> BytesIO:
    buf = BytesIO()
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5, leading=11, fontName=REG)
    right = ParagraphStyle("right", parent=small, alignment=TA_RIGHT)
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=16 * mm, rightMargin=16 * mm, title=f"Invoice {inv.number or 'draft'}", author=cfg.legal_name or tenant.name)
    story = []

    # ---- header: supplier + invoice meta ----
    supplier_name = cfg.legal_name or tenant.name
    sup = (f"<b>{e(supplier_name)}</b><br/>{e(cfg.address or tenant.address)}<br/>"
           + (f"State: {e(STATE_CODES.get(cfg.state_code, ''))} ({e(cfg.state_code)})<br/>" if cfg.state_code else "")
           + (f"GSTIN: {e(tenant.gstin)}<br/>" if tenant.gstin else "") + (f"PAN: {e(cfg.pan)}<br/>" if cfg.pan else "")
           + (f"{e(cfg.email)} " if cfg.email else "") + (f"· {e(cfg.phone)}" if cfg.phone else "") + (f"<br/><b>{e(cfg.website)}</b>" if cfg.website else ""))
    title = "TAX INVOICE" if tenant.gstin else "INVOICE"
    stamp = {"paid": "PAID", "part_paid": "PARTLY PAID", "cancelled": "CANCELLED", "draft": "DRAFT"}.get(inv.status, "")
    meta = (f"<b>{title}</b>" + (f" &nbsp;<font color='#B42318'><b>{stamp}</b></font>" if stamp else "") + "<br/><br/>"
            f"Invoice No. : <b>{e(inv.number or 'DRAFT')}</b><br/>Invoice Date : {inv.issue_date:%d %b %Y}<br/>Due Date : {inv.due_date:%d %b %Y}<br/>"
            f"Place of Supply : {e(STATE_CODES.get(inv.place_of_supply, '—'))}" + (f" ({e(inv.place_of_supply)})" if inv.place_of_supply else "")
            + (f"<br/>Period : {inv.period_start:%d %b %Y} – {inv.period_end:%d %b %Y}" if inv.period_start and inv.period_end else "")
            + (f"<br/>Contract : {e(inv.contract.number)}" if inv.contract_id else ""))
    logo_cell = ""
    logo_path = cfg.logo.path if cfg.logo else os.path.join(settings.BASE_DIR, "static", "img", "logo-mark.png")
    if logo_path and os.path.exists(logo_path):
        try:
            img = Image(logo_path)
            ratio = img.imageHeight / float(img.imageWidth)
            img.drawWidth, img.drawHeight = 38 * mm, 38 * mm * ratio
            img.hAlign = "LEFT"
            logo_cell = img
        except Exception:
            logo_cell = ""
    head = Table([[logo_cell, Paragraph(meta, right)], [Paragraph(sup, small), ""]], colWidths=[105 * mm, 74 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("SPAN", (1, 0), (1, 1)), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 6 * mm)]

    # ---- parties ----
    def party(label, name, address, gstin="", state="", email=""):
        return Paragraph(f"<b>{label}</b><br/><br/><b>{e(name)}</b><br/>{e(address)}<br/>"
                         + (f"State: {e(STATE_CODES.get(state, state))} ({e(state)})<br/>" if state else "") + (f"GSTIN: {e(gstin)}<br/>" if gstin else "") + (e(email) if email else ""), small)

    bill = party("BILL TO", inv.bill_to_name, inv.bill_to_address, inv.bill_to_gstin, inv.bill_to_state, inv.bill_to_email)
    ship = party("SHIP TO", inv.ship_to_name or inv.bill_to_name, inv.ship_to_address or inv.bill_to_address, "", inv.place_of_supply)
    pt = Table([[bill, ship]], colWidths=[89.5 * mm, 89.5 * mm])
    pt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [pt, Spacer(1, 7 * mm)]

    # ---- lines ----
    rows = [["S.NO.", "ITEM", "HSN/SAC", "QTY", "RATE", "TAX", "AMOUNT"]]
    for i, l in enumerate(inv.lines.all(), 1):
        rows.append([str(i), Paragraph(f"<b>{e(l.description)}</b>", small), l.hsn_sac or "-", f"{float(l.quantity):g}{(' ' + l.unit) if l.unit and l.unit != 'nos' else ''}", money(l.rate_paise), _pct(l.gst_rate), money(l.amount_paise + l.tax_paise)])
    tbl = Table(rows, colWidths=[12 * mm, 68 * mm, 18 * mm, 14 * mm, 24 * mm, 15 * mm, 28 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")), ("FONTNAME", (0, 0), (-1, -1), REG), ("FONTNAME", (0, 0), (-1, 0), BOLD), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (0, -1), "CENTER"), ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), .75, colors.HexColor("#cccccc")), ("LINEBELOW", (0, 1), (-1, -1), .4, colors.HexColor("#eeeeee")), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)

    inter = inv.tax_mode == "inter"
    st = Table([["SUBTOTAL", "IGST" if inter else "CGST + SGST", money(inv.tax_paise), money(inv.subtotal_paise + inv.tax_paise)]], colWidths=[94 * mm, 24 * mm, 24 * mm, 37 * mm])
    st.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f7f7")), ("FONTNAME", (0, 0), (-1, -1), BOLD), ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [st, Spacer(1, 6 * mm)]

    # ---- notes/terms + totals ----
    left = []
    if inv.notes:
        left.append(f"<b>NOTES</b><br/>{e(inv.notes)}<br/><br/>")
    terms = [t.strip() for t in (inv.terms or "").splitlines() if t.strip()]
    if terms:
        left.append("<b>TERMS AND CONDITIONS</b><br/>" + "<br/>".join(f"• {e(t)}" for t in terms))
    totals = [["Taxable Amount", money(inv.subtotal_paise)]]
    for rate, taxable, tax in inv.tax_summary():
        if inter:
            totals.append([f"IGST @{_pct(rate)}", money(tax)])
        else:
            h = tax // 2
            totals += [[f"CGST @{_pct(rate / 2)}", money(h)], [f"SGST @{_pct(rate / 2)}", money(tax - h)]]
    if inv.round_off_paise:
        totals.append(["Round off", money(inv.round_off_paise)])
    bold_rows = [len(totals)]
    totals.append(["Total Amount", money(inv.total_paise)])
    totals.append(["Received", money(inv.received_paise)])
    bold_rows.append(len(totals))
    totals.append(["Balance Due", money(inv.balance_paise)])
    tt = Table(totals, colWidths=[38 * mm, 40 * mm])
    style = [("FONTNAME", (0, 0), (-1, -1), REG), ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT")]
    for r in bold_rows:
        style += [("FONTNAME", (0, r), (-1, r), BOLD), ("LINEABOVE", (0, r), (-1, r), .75, colors.HexColor("#999999"))]
    tt.setStyle(TableStyle(style))
    words = Paragraph(f"<i>Total Amount (in words):</i><br/>{e(amount_to_words(inv.total_paise))}", ParagraphStyle("words", parent=small, alignment=TA_RIGHT, fontSize=8))
    rt = Table([[tt], [Spacer(1, 4)], [words]], colWidths=[78 * mm])
    bt = Table([[Paragraph("".join(left), small), rt]], colWidths=[100 * mm, 79 * mm])
    bt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    story += [bt, Spacer(1, 10 * mm), HRFlowable(width="100%", color=colors.HexColor("#dddddd")), Spacer(1, 5 * mm)]

    # ---- bank + signature (nested table, never KeepTogether in a cell: ReportLab mis-measures it) ----
    bank = ""
    if cfg.bank_account_no or cfg.upi_id:
        bank = ("<b>BANK DETAILS</b><br/>" + (f"Name: {e(cfg.bank_account_name or supplier_name)}<br/>") + (f"Bank: {e(cfg.bank_name)}<br/>" if cfg.bank_name else "")
                + (f"Account No.: {e(cfg.bank_account_no)}<br/>" if cfg.bank_account_no else "") + (f"IFSC Code: {e(cfg.bank_ifsc)}<br/>" if cfg.bank_ifsc else "") + (f"UPI ID: {e(cfg.upi_id)}" if cfg.upi_id else ""))
    sig = Table([[Spacer(1, 14 * mm)], [Paragraph(f"Authorised Signatory for<br/><b>{e(supplier_name)}</b>", right)]], colWidths=[74 * mm])
    sig.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    ft = Table([[Paragraph(bank, small), "", sig]], colWidths=[80 * mm, 25 * mm, 74 * mm], hAlign="LEFT")
    ft.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.append(ft)
    story += [Spacer(1, 6 * mm), Paragraph("This is a computer-generated invoice. Generated by QuantumLoop ERP.", ParagraphStyle("foot", parent=small, fontSize=7, textColor=colors.HexColor("#888888")))]

    doc.build(story)
    buf.seek(0)
    return buf
