import streamlit as st
import boto3
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import re
import pandas as pd
import tempfile
from PIL import Image
import io

# Data models for our receipt items
@dataclass
class ReceiptItem:
    name: str
    price: float
    quantity: int = 1
    assigned_to: List[str] = field(default_factory=list)
    
    def assign_to(self, person: str):
        if person not in self.assigned_to:
            self.assigned_to.append(person)
    
    def unassign_from(self, person: str):
        if person in self.assigned_to:
            self.assigned_to.remove(person)
    
    def price_per_person(self) -> float:
        if not self.assigned_to:
            return 0.0
        return self.price / len(self.assigned_to)


@dataclass
class Receipt:
    items: List[ReceiptItem] = field(default_factory=list)
    date: str = ""
    restaurant_name: str = ""
    subtotal: float = 0.0
    tax: float = 0.0
    tip: float = 0.0
    
    def total(self) -> float:
        return self.subtotal + self.tax + self.tip
    
    def add_item(self, item: ReceiptItem):
        self.items.append(item)
    
    def get_person_totals(self) -> Dict[str, float]:
        """Calculate how much each person owes"""
        person_totals = {}
        
        # Sum up item costs for each person
        for item in self.items:
            for person in item.assigned_to:
                if person not in person_totals:
                    person_totals[person] = 0.0
                person_totals[person] += item.price_per_person()
        
        # Distribute tax and tip proportionally
        if self.subtotal > 0:
            for person in person_totals:
                # Calculate person's percentage of subtotal
                person_subtotal_percentage = person_totals[person] / self.subtotal
                # Add proportional tax and tip
                person_totals[person] += (self.tax + self.tip) * person_subtotal_percentage
        
        return person_totals


class ReceiptParser:
    def __init__(self, aws_access_key: Optional[str] = None, aws_secret_key: Optional[str] = None, region: str = 'us-east-1'):
        """Initialize the AWS Textract client"""
        # If keys are provided, use them; otherwise, use the AWS CLI configuration
        if aws_access_key and aws_secret_key:
            self.textract = boto3.client(
                'textract',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
                region_name=region
            )
        else:
            try:
                self.textract = boto3.client('textract', region_name=region)
            except Exception as e:
                st.error(f"Error initializing AWS Textract: {str(e)}")
                self.textract = None
    
    def scan_receipt(self, image_bytes: bytes) -> Dict[str, Any]:
        """Scan the receipt image using AWS Textract and return the raw response"""
        if self.textract is None:
            raise Exception("AWS Textract client not initialized")
            
        response = self.textract.analyze_expense(
            Document={'Bytes': image_bytes}
        )
        
        return response
    
    def parse_textract_response(self, response: Dict[str, Any]) -> Receipt:
        """Parse the AWS Textract response into a Receipt object"""
        receipt = Receipt()
        
        # Extract information from the Textract response
        if 'ExpenseDocuments' in response:
            for doc in response['ExpenseDocuments']:
                # Extract overall receipt information
                if 'SummaryFields' in doc:
                    for field in doc['SummaryFields']:
                        if field.get('Type', {}).get('Text') == 'VENDOR_NAME':
                            receipt.restaurant_name = field.get('ValueDetection', {}).get('Text', '')
                        elif field.get('Type', {}).get('Text') == 'RECEIPT_DATE':
                            receipt.date = field.get('ValueDetection', {}).get('Text', '')
                        elif field.get('Type', {}).get('Text') == 'SUBTOTAL':
                            subtotal_text = field.get('ValueDetection', {}).get('Text', '0.0')
                            receipt.subtotal = self._extract_price(subtotal_text)
                        elif field.get('Type', {}).get('Text') == 'TAX':
                            tax_text = field.get('ValueDetection', {}).get('Text', '0.0')
                            receipt.tax = self._extract_price(tax_text)
                
                # Extract line items
                if 'LineItemGroups' in doc:
                    for group in doc['LineItemGroups']:
                        if 'LineItems' in group:
                            for line_item in group['LineItems']:
                                item_name = ""
                                item_price = 0.0
                                item_quantity = 1
                                
                                if 'LineItemExpenseFields' in line_item:
                                    for field in line_item['LineItemExpenseFields']:
                                        if field.get('Type', {}).get('Text') == 'ITEM':
                                            item_name = field.get('ValueDetection', {}).get('Text', '')
                                        elif field.get('Type', {}).get('Text') == 'PRICE':
                                            price_text = field.get('ValueDetection', {}).get('Text', '0.0')
                                            item_price = self._extract_price(price_text)
                                        elif field.get('Type', {}).get('Text') == 'QUANTITY':
                                            qty_text = field.get('ValueDetection', {}).get('Text', '1')
                                            try:
                                                item_quantity = int(re.sub(r'[^\d]', '', qty_text) or 1)
                                            except ValueError:
                                                item_quantity = 1
                                
                                if item_name and item_price > 0:
                                    receipt.add_item(ReceiptItem(
                                        name=item_name,
                                        price=item_price,
                                        quantity=item_quantity
                                    ))
        
        # If subtotal wasn't found, calculate it from items
        if receipt.subtotal == 0.0 and receipt.items:
            receipt.subtotal = sum(item.price for item in receipt.items)
        
        return receipt
    
    def _extract_price(self, text: str) -> float:
        """Extract a price from text, handling different formats"""
        if not text:
            return 0.0
        
        # Remove currency symbols and commas
        cleaned_text = re.sub(r'[^\d\.\-]', '', text)
        
        try:
            return float(cleaned_text)
        except ValueError:
            return 0.0


# Function to create mock receipt data for testing without AWS
def create_mock_receipt():
    receipt = Receipt()
    receipt.restaurant_name = "Test Restaurant"
    receipt.date = "2025-03-06"
    receipt.subtotal = 45.00
    receipt.tax = 3.60
    
    # Add some sample items
    items = [
        ReceiptItem(name="Burger", price=12.99, quantity=1),
        ReceiptItem(name="Pizza", price=15.50, quantity=1),
        ReceiptItem(name="Salad", price=8.99, quantity=1),
        ReceiptItem(name="Drink", price=3.99, quantity=2),
    ]
    
    for item in items:
        receipt.add_item(item)
    
    return receipt


# Function to set the current step
def set_step(step_number):
    st.session_state.current_step = step_number


# Streamlit app
def main():
    st.set_page_config(
        page_title="Receipt Splitter",
        page_icon="🧾",
        layout="wide"
    )
    
    # Initialize session state variables if they don't exist
    if 'receipt' not in st.session_state:
        st.session_state.receipt = None
    if 'people' not in st.session_state:
        st.session_state.people = []
    if 'current_step' not in st.session_state:
        st.session_state.current_step = 1
    if 'parser' not in st.session_state:
        st.session_state.parser = ReceiptParser()
    if 'mock_mode' not in st.session_state:
        st.session_state.mock_mode = False
    
    # App title and description
    st.title("💰 Receipt Splitter 💰")
    st.write("Upload a receipt image, then assign items to people to split the bill fairly.")
    
    # Steps progress bar
    steps = ["Upload Receipt", "Add People", "Assign Items", "Summary"]
    current_step_idx = st.session_state.current_step - 1
    
    # Progress bar
    st.progress(current_step_idx / len(steps))
    
    # Current step indicator
    st.subheader(f"Step {st.session_state.current_step}: {steps[current_step_idx]}")
    
    # Sidebar for navigation
    with st.sidebar:
        st.header("Navigation")
        for i, step in enumerate(steps, 1):
            if i <= max(st.session_state.current_step, 1):  # Only enable completed steps
                if st.button(f"{i}. {step}", key=f"nav_{i}"):
                    set_step(i)
                    st.rerun()
        
        # Debug mode toggle
        st.divider()
        if st.checkbox("Use test data (no AWS required)", value=st.session_state.mock_mode):
            st.session_state.mock_mode = True
            if st.button("Load Test Data"):
                st.session_state.receipt = create_mock_receipt()
                st.success("Test data loaded!")
                st.rerun()
        else:
            st.session_state.mock_mode = False
    
    # Main app flow - Step 1: Receipt Upload
    if st.session_state.current_step == 1:
        # AWS credentials section (collapsible)
        with st.expander("AWS Configuration (Optional)"):
            st.write("If you haven't configured AWS CLI, you can enter your credentials here.")
            aws_region = st.text_input("AWS Region (default: us-east-1)", value="us-east-1")
            aws_access_key = st.text_input("AWS Access Key ID (optional)")
            aws_secret_key = st.text_input("AWS Secret Access Key (optional)", type="password")
            
            if st.button("Update AWS Configuration"):
                if aws_access_key and aws_secret_key:
                    st.session_state.parser = ReceiptParser(
                        aws_access_key=aws_access_key,
                        aws_secret_key=aws_secret_key,
                        region=aws_region
                    )
                    st.success("AWS credentials updated!")
        
        # Receipt upload
        uploaded_file = st.file_uploader("Upload receipt image", type=["jpg", "jpeg", "png", "pdf"])
        
        if uploaded_file:
            # Display uploaded image
            image_data = uploaded_file.getvalue()
            st.image(image_data, caption="Uploaded receipt", width=400)
            
            # Process the receipt
            if st.button("Scan Receipt", key="scan_receipt"):
                with st.spinner("Scanning receipt with AWS Textract..."):
                    try:
                        # Process the image with Textract
                        response = st.session_state.parser.scan_receipt(image_data)
                        receipt = st.session_state.parser.parse_textract_response(response)
                        
                        # Store the receipt in session state
                        st.session_state.receipt = receipt
                        
                        # Show success and receipt details
                        st.success("Receipt scanned successfully!")
                        st.write(f"Restaurant: {receipt.restaurant_name}")
                        st.write(f"Date: {receipt.date}")
                        st.write(f"Items found: {len(receipt.items)}")
                        
                        # Show items in a table
                        if receipt.items:
                            items_df = pd.DataFrame([
                                {"Item": item.name, "Price": f"${item.price:.2f}", "Quantity": item.quantity}
                                for item in receipt.items
                            ])
                            st.write("Items detected:")
                            st.dataframe(items_df)
                        
                    except Exception as e:
                        st.error(f"Error processing receipt: {str(e)}")
                        st.info("Try using the 'Use test data' option in the sidebar if you're having trouble with AWS.")
        
        # If we have a receipt (either from scanning or test data), show adjustment options
        if st.session_state.receipt:
            receipt = st.session_state.receipt
            
            # Manual receipt adjustments
            with st.expander("Adjust Receipt Details"):
                col1, col2 = st.columns(2)
                with col1:
                    new_restaurant = st.text_input("Restaurant Name", value=receipt.restaurant_name)
                    new_date = st.text_input("Date", value=receipt.date)
                with col2:
                    new_subtotal = st.number_input("Subtotal ($)", value=receipt.subtotal, min_value=0.0, step=0.01)
                    new_tax = st.number_input("Tax ($)", value=receipt.tax, min_value=0.0, step=0.01)
                
                if st.button("Update Receipt Details"):
                    receipt.restaurant_name = new_restaurant
                    receipt.date = new_date
                    receipt.subtotal = new_subtotal
                    receipt.tax = new_tax
                    st.success("Receipt details updated!")
            
            # Allow adding items manually
            with st.expander("Add Item Manually"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    new_item_name = st.text_input("Item Name")
                with col2:
                    new_item_price = st.number_input("Price ($)", min_value=0.0, step=0.01)
                with col3:
                    new_item_quantity = st.number_input("Quantity", min_value=1, step=1, value=1)
                
                if st.button("Add Item"):
                    if new_item_name and new_item_price > 0:
                        receipt.add_item(ReceiptItem(
                            name=new_item_name,
                            price=new_item_price,
                            quantity=new_item_quantity
                        ))
                        st.success(f"Added: {new_item_name} - ${new_item_price:.2f}")
                        # Update the items table
                        if receipt.items:
                            items_df = pd.DataFrame([
                                {"Item": item.name, "Price": f"${item.price:.2f}", "Quantity": item.quantity}
                                for item in receipt.items
                            ])
                            st.write("Current Items:")
                            st.dataframe(items_df)
            
            # Continue button
            if st.button("Continue to Add People", key="continue_to_people"):
                set_step(2)
                st.rerun()
    
    # Step 2: Adding People
    elif st.session_state.current_step == 2:
        if st.session_state.receipt is None:
            st.warning("Please upload and scan a receipt first.")
            if st.button("Go Back to Upload Receipt"):
                set_step(1)
                st.rerun()
        else:
            # Display the people already added
            if st.session_state.people:
                st.write("People added:")
                people_cols = st.columns(3)
                for i, person in enumerate(st.session_state.people):
                    col_idx = i % 3
                    with people_cols[col_idx]:
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.write(f"{i+1}. {person}")
                        with col2:
                            if st.button("❌", key=f"remove_{i}"):
                                st.session_state.people.pop(i)
                                st.rerun()
            
            # Add new person
            col1, col2 = st.columns([3, 1])
            with col1:
                new_person = st.text_input("Enter a person's name")
            with col2:
                st.write("")
                st.write("")
                if st.button("Add", key="add_person"):
                    if new_person and new_person not in st.session_state.people:
                        st.session_state.people.append(new_person)
                        st.rerun()
            
            # Quick add multiple people
            with st.expander("Add Multiple People"):
                people_text = st.text_area("Enter names separated by commas")
                if st.button("Add All"):
                    if people_text:
                        new_people = [p.strip() for p in people_text.split(",") if p.strip()]
                        for person in new_people:
                            if person not in st.session_state.people:
                                st.session_state.people.append(person)
                        st.rerun()
            
            # Navigation buttons
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Back to Receipt", key="back_to_receipt"):
                    set_step(1)
                    st.rerun()
            with col2:
                if st.button("Continue to Assign Items", key="continue_to_assign"):
                    if st.session_state.people:
                        set_step(3)
                        st.rerun()
                    else:
                        st.warning("Please add at least one person before continuing.")
    
    # Step 3: Assign Items
    elif st.session_state.current_step == 3:
        if not st.session_state.receipt or not st.session_state.people:
            st.warning("Please complete the previous steps first.")
            if st.button("Go Back to Add People"):
                set_step(2)
                st.rerun()
        else:
            receipt = st.session_state.receipt
            people = st.session_state.people
            
            # Add tip information
            col1, col2 = st.columns(2)
            with col1:
                tip_option = st.radio("Add tip as:", ["Percentage", "Fixed Amount"])
            with col2:
                if tip_option == "Percentage":
                    tip_value = st.number_input("Tip percentage", min_value=0.0, value=15.0, step=1.0)
                    receipt.tip = receipt.subtotal * (tip_value / 100)
                else:
                    receipt.tip = st.number_input("Tip amount ($)", min_value=0.0, value=receipt.tip, step=1.0)
            
            st.write(f"Tip: ${receipt.tip:.2f}")
            
            # Display items with assignment checkboxes
            st.subheader("Assign items to people")
            st.write("Check the boxes for each person who shared an item.")
            
            for i, item in enumerate(receipt.items):
                with st.expander(f"{item.name} - ${item.price:.2f}"):
                    # Create a row of checkboxes for each person
                    cols = st.columns(len(people))
                    for j, person in enumerate(people):
                        with cols[j]:
                            is_assigned = person in item.assigned_to
                            if st.checkbox(person, value=is_assigned, key=f"item_{i}_person_{j}"):
                                if person not in item.assigned_to:
                                    item.assign_to(person)
                            else:
                                if person in item.assigned_to:
                                    item.unassign_from(person)
                    
                    # Show current assignments
                    if item.assigned_to:
                        st.write(f"Assigned to: {', '.join(item.assigned_to)}")
                    else:
                        st.warning("This item is not assigned to anyone.")
            
            # Quick assign options
            with st.expander("Quick Assignment Tools"):
                st.subheader("Assign all items to someone")
                person_to_assign = st.selectbox("Select person", options=people)
                if st.button("Assign All Unassigned Items"):
                    for item in receipt.items:
                        if not item.assigned_to:
                            item.assign_to(person_to_assign)
                    st.success("Unassigned items have been assigned!")
                
                st.subheader("Split Everything Equally")
                if st.button("Assign All Items to Everyone"):
                    for item in receipt.items:
                        for person in people:
                            item.assign_to(person)
                    st.success("All items assigned to everyone!")
            
            # Navigation buttons
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Back to People", key="back_to_people"):
                    set_step(2)
                    st.rerun()
            with col2:
                if st.button("Continue to Summary", key="continue_to_summary"):
                    # Check if all items are assigned
                    unassigned = [item for item in receipt.items if not item.assigned_to]
                    if unassigned:
                        st.warning(f"There are {len(unassigned)} unassigned items. Are you sure you want to continue?")
                        if st.button("Yes, Continue Anyway"):
                            set_step(4)
                            st.rerun()
                    else:
                        set_step(4)
                        st.rerun()
    
    # Step 4: Review and Summary
    elif st.session_state.current_step == 4:
        if not st.session_state.receipt:
            st.warning("No receipt data available. Please start over.")
            if st.button("Start Over", key="start_over"):
                st.session_state.clear()
                st.rerun()
        else:
            receipt = st.session_state.receipt
            
            # Receipt summary
            st.subheader("Receipt Details")
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Restaurant:** {receipt.restaurant_name}")
                st.write(f"**Date:** {receipt.date}")
                st.write(f"**Items:** {len(receipt.items)}")
            with col2:
                st.write(f"**Subtotal:** ${receipt.subtotal:.2f}")
                st.write(f"**Tax:** ${receipt.tax:.2f}")
                st.write(f"**Tip:** ${receipt.tip:.2f}")
                st.write(f"**Total:** ${receipt.total():.2f}")
            
            # Display assigned items
            st.subheader("Item Assignments")
            
            items_data = []
            for item in receipt.items:
                assigned_to = ", ".join(item.assigned_to) if item.assigned_to else "Unassigned"
                items_data.append({
                    "Item": item.name,
                    "Price": f"${item.price:.2f}",
                    "Assigned To": assigned_to
                })
            
            if items_data:
                st.dataframe(pd.DataFrame(items_data))
            
            # Calculate what each person owes
            st.subheader("💵 Split Summary 💵")
            person_totals = receipt.get_person_totals()
            
            # Display as a table with a bar chart
            split_data = []
            for person, amount in person_totals.items():
                split_data.append({"Person": person, "Amount": round(amount, 2)})
            
            if split_data:
                split_df = pd.DataFrame(split_data)
                
                # Create columns for the table and chart
                col1, col2 = st.columns([1, 2])
                
                with col1:
                    # Table with amounts
                    st.dataframe(split_df.style.format({"Amount": "${:.2f}"}))
                
                with col2:
                    # Bar chart
                    st.bar_chart(data=split_df, x="Person", y="Amount", use_container_width=True)
            
            # Share options
            st.subheader("Share with Friends")
            st.write("You can save a summary of this split to share with friends.")
            
            # Generate a text summary
            summary_text = f"Receipt Split Summary\n\n"
            summary_text += f"Restaurant: {receipt.restaurant_name}\n"
            summary_text += f"Date: {receipt.date}\n"
            summary_text += f"Total: ${receipt.total():.2f}\n\n"
            
            summary_text += "What Each Person Owes:\n"
            for person, amount in person_totals.items():
                summary_text += f"- {person}: ${amount:.2f}\n"
            
            # Display summary and provide download
            st.text_area("Summary Text", summary_text, height=200)
            
            # Convert summary to CSV for download
            csv = "Person,Amount\n"
            for person, amount in person_totals.items():
                csv += f"{person},${amount:.2f}\n"
            
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name="receipt_split.csv",
                mime="text/csv"
            )
            
            # Navigation buttons
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Back to Assignments", key="back_to_assign"):
                    set_step(3)
                    st.rerun()
            with col2:
                if st.button("Start Over", key="reset_app"):
                    st.session_state.clear()
                    st.rerun()


if __name__ == "__main__":
    main()