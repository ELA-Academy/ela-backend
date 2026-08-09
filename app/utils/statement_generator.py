import os
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# Helper to format currency
def format_currency(val):
    if val is None:
        return "$0.00"
    abs_val = abs(float(val))
    formatted = f"${abs_val:,.2f}"
    return f"({formatted})" if float(val) < 0 else formatted

def generate_statement_pdf(student, transactions, start_date, end_date, starting_balance, summary):
    """
    Generates a bank-statement-like PDF of the student's ledger.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0b2f4c'),
        spaceAfter=15
    )
    
    section_title = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#5b6f82'),
        spaceBefore=10,
        spaceAfter=5
    )
    
    normal_text = ParagraphStyle(
        'NormalText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#333333')
    )
    
    bold_text = ParagraphStyle(
        'BoldText',
        parent=normal_text,
        fontName='Helvetica-Bold'
    )
    
    header_right = ParagraphStyle(
        'HeaderRight',
        parent=normal_text,
        alignment=2, # Right align
        fontSize=8,
        textColor=colors.HexColor('#64748b')
    )

    story = []
    
    # Header block
    school_name = "<b>Exceptional Learning and Arts Academy</b>"
    school_address = "P.O. Box 29515<br/>Jacksonville, FL 32256"
    
    header_data = [
        [Paragraph(school_name + "<br/>" + school_address, normal_text), 
         Paragraph("<b>STATEMENT OF ACCOUNT</b><br/>Period: " + start_date.strftime('%b %d, %Y') + " - " + end_date.strftime('%b %d, %Y'), header_right)]
    ]
    header_table = Table(header_data, colWidths=[4.0*inch, 3.5*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))
    
    # Horizontal line
    line_table = Table([[""]], colWidths=[7.5*inch])
    line_table.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 1, colors.HexColor('#e6eaf0')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 10))
    
    # Account & Student info
    parent_list = ", ".join([f"{p.first_name} {p.last_name}" for p in student.parents]) or "N/A"
    info_data = [
        [Paragraph("<b>Student:</b>", bold_text), Paragraph(f"{student.first_name} {student.last_name}", normal_text),
         Paragraph("<b>Statement Date:</b>", bold_text), Paragraph(datetime.now().strftime('%b %d, %Y'), normal_text)],
        [Paragraph("<b>Parents:</b>", bold_text), Paragraph(parent_list, normal_text),
         Paragraph("<b>Classroom:</b>", bold_text), Paragraph(student.grade_level, normal_text)]
    ]
    info_table = Table(info_data, colWidths=[1.0*inch, 2.75*inch, 1.25*inch, 2.5*inch])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 15))
    
    # Summary of Account Block
    ending_balance = starting_balance + summary['invoiced'] - (summary['paid'] + summary['credited'])
    
    summary_headers = [
        Paragraph("Starting Balance", bold_text),
        Paragraph("+ Total Invoiced", bold_text),
        Paragraph("- Total Payments", bold_text),
        Paragraph("- Total Credits", bold_text),
        Paragraph("Ending Balance", bold_text)
    ]
    
    summary_values = [
        Paragraph(format_currency(starting_balance), normal_text),
        Paragraph(format_currency(summary['invoiced']), normal_text),
        Paragraph(format_currency(summary['paid']), normal_text),
        Paragraph(format_currency(summary['credited']), normal_text),
        Paragraph(format_currency(ending_balance), bold_text)
    ]
    
    summary_table = Table([summary_headers, summary_values], colWidths=[1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#fafafa')),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e6eaf0')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(Paragraph("<b>Account Summary</b>", section_title))
    story.append(summary_table)
    story.append(Spacer(1, 15))
    
    # Detailed Transactions List
    tx_headers = ["Date", "Type", "Description", "Amount", "Balance"]
    tx_data = [[Paragraph(f"<b>{h}</b>", bold_text if h != "Balance" else ParagraphStyle('Bal', parent=bold_text, alignment=2)) for h in tx_headers]]
    
    current_bal = starting_balance
    for tx in transactions:
        tx_date = datetime.strptime(tx['date'].split('T')[0], '%Y-%m-%d').strftime('%b %d, %Y')
        tx_type = tx['type']
        tx_desc = tx['description']
        
        # In ledger response: Invoices are positive amount, payments/credits are negative amount.
        # Let's show positive absolute amount for individual items in column,
        # but display sign or format depending on type.
        amt_raw = float(tx['amount'])
        if tx_type == "Invoice":
            amt_str = format_currency(amt_raw)
            current_bal += amt_raw
        else:
            # show payments/credits as negative
            amt_str = format_currency(amt_raw)
            current_bal += amt_raw # amt_raw is already negative for payments/credits in ledger response
            
        row = [
            Paragraph(tx_date, normal_text),
            Paragraph(tx_type, normal_text),
            Paragraph(tx_desc, normal_text),
            Paragraph(amt_str, normal_text),
            Paragraph(format_currency(current_bal), bold_text)
        ]
        tx_data.append(row)
        
    # Table layout
    tx_table = Table(tx_data, colWidths=[1.1*inch, 0.9*inch, 3.3*inch, 1.1*inch, 1.1*inch])
    tx_table_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f1f5f9')),
        ('ALIGN', (0,0), (-1,0), 'LEFT'),
        ('ALIGN', (3,0), (4,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
    ]
    tx_table.setStyle(TableStyle(tx_table_style))
    
    story.append(Paragraph("<b>Transaction Ledger</b>", section_title))
    story.append(tx_table)
    
    # Build Document
    doc.build(story)
    buffer.seek(0)
    return buffer

def generate_receipt_pdf(student, payment):
    """
    Generates a payment receipt PDF.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#0b2f4c'),
        spaceAfter=15
    )
    
    normal_text = ParagraphStyle(
        'NormalText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#333333')
    )
    
    bold_text = ParagraphStyle(
        'BoldText',
        parent=normal_text,
        fontName='Helvetica-Bold'
    )
    
    header_right = ParagraphStyle(
        'HeaderRight',
        parent=normal_text,
        alignment=2,
        fontSize=9,
        textColor=colors.HexColor('#64748b')
    )

    story = []
    
    # Header block
    school_name = "<b>Exceptional Learning and Arts Academy</b>"
    school_address = "P.O. Box 29515<br/>Jacksonville, FL 32256"
    receipt_id = f"#PYMT-{payment.id:08d}" if payment.id else "#PYMT-TEMP"
    
    header_data = [
        [Paragraph(school_name + "<br/>" + school_address, normal_text), 
         Paragraph(f"<b>RECEIPT: {receipt_id}</b>", header_right)]
    ]
    header_table = Table(header_data, colWidths=[4.0*inch, 3.5*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))
    
    # Horizontal line
    line_table = Table([[""]], colWidths=[7.5*inch])
    line_table.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 1, colors.HexColor('#e6eaf0')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 15))
    
    # Payment info details
    tx_date = payment.transaction_date.strftime('%B %d, %Y')
    parent_list = ", ".join([f"{p.first_name} {p.last_name}" for p in student.parents]) or "N/A"
    
    info_data = [
        [Paragraph("<b>Payment For:</b>", bold_text), Paragraph(f"{student.first_name} {student.last_name}", normal_text)],
        [Paragraph("<b>Parents/Payer:</b>", bold_text), Paragraph(parent_list, normal_text)],
        [Paragraph("<b>Payment Date:</b>", bold_text), Paragraph(tx_date, normal_text)],
        [Paragraph("<b>Payment Method:</b>", bold_text), Paragraph(payment.method, normal_text)],
    ]
    info_table = Table(info_data, colWidths=[1.5*inch, 6.0*inch])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 20))
    
    # Receipt line table
    receipt_headers = ["Description", "Amount Paid"]
    receipt_rows = [[Paragraph(f"<b>{h}</b>", bold_text) for h in receipt_headers]]
    
    desc = f"Payment via {payment.method}"
    if payment.notes:
        desc += f" ({payment.notes})"
        
    receipt_rows.append([
        Paragraph(desc, normal_text),
        Paragraph(format_currency(payment.amount), normal_text)
    ])
    
    receipt_rows.append([
        Paragraph("<b>TOTAL AMOUNT</b>", bold_text),
        Paragraph(f"<b>{format_currency(payment.amount)}</b>", bold_text)
    ])
    
    receipt_table = Table(receipt_rows, colWidths=[5.5*inch, 2.0*inch])
    receipt_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f1f5f9')),
        ('ALIGN', (0,0), (-1,0), 'LEFT'),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#ecfdf5')), # light green bg for total
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
    ]))
    
    story.append(receipt_table)
    
    # Build Document
    doc.build(story)
    buffer.seek(0)
    return buffer
