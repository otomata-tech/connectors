"""Google Sheets API client."""

import csv
import io
import re
from typing import Optional, List, Any

from googleapiclient.discovery import build

from oto.tools.common.credentials import require


SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


#: A bare A1 range (`A:R`, `B5:D`, `5:9`), as opposed to a bare sheet name.
_A1 = re.compile(r'[A-Za-z]{0,3}\d*(:[A-Za-z]{0,3}\d*)?')


class SheetsClientError(Exception):
    pass


class SheetsClient:
    """Google Sheets API client."""

    def __init__(self, credentials=None):
        """`credentials`: Google credentials provided by the consumer (required)."""
        self.service = build('sheets', 'v4', credentials=require(credentials, 'GOOGLE_CREDENTIALS'))
        self.sheets = self.service.spreadsheets()

    def create(self, title: str) -> dict:
        """Create a new empty spreadsheet."""
        result = self.sheets.create(
            body={'properties': {'title': title}},
            fields='spreadsheetId,spreadsheetUrl,properties.title',
        ).execute()
        return {
            'id': result['spreadsheetId'],
            'title': result['properties']['title'],
            'url': result['spreadsheetUrl'],
        }

    def get_metadata(self, spreadsheet_id: str) -> dict:
        """Get spreadsheet metadata (title, sheets, etc.)."""
        result = self.sheets.get(
            spreadsheetId=spreadsheet_id,
            fields='spreadsheetId,properties.title,sheets.properties'
        ).execute()
        return {
            'id': result['spreadsheetId'],
            'title': result['properties']['title'],
            'sheets': [
                {
                    'id': s['properties']['sheetId'],
                    'title': s['properties']['title'],
                    'rows': s['properties'].get('gridProperties', {}).get('rowCount'),
                    'cols': s['properties'].get('gridProperties', {}).get('columnCount'),
                }
                for s in result.get('sheets', [])
            ]
        }

    def read(
        self,
        spreadsheet_id: str,
        range: str = 'A:ZZ',
        value_render: str = 'FORMATTED_VALUE',
    ) -> List[List[Any]]:
        """Read values from a range. Returns list of rows."""
        result = self.sheets.values().get(
            spreadsheetId=spreadsheet_id,
            range=range,
            valueRenderOption=value_render,
        ).execute()
        return result.get('values', [])

    def write(
        self,
        spreadsheet_id: str,
        range: str,
        values: List[List[Any]],
        value_input: str = 'USER_ENTERED',
    ) -> dict:
        """Write values to a range (overwrites existing data)."""
        result = self.sheets.values().update(
            spreadsheetId=spreadsheet_id,
            range=range,
            valueInputOption=value_input,
            body={'values': values},
        ).execute()
        return {
            'updated_range': result.get('updatedRange'),
            'updated_rows': result.get('updatedRows'),
            'updated_cols': result.get('updatedColumns'),
            'updated_cells': result.get('updatedCells'),
        }

    def append(
        self,
        spreadsheet_id: str,
        range: str,
        values: List[List[Any]],
        value_input: str = 'USER_ENTERED',
    ) -> dict:
        """Append rows after existing data, starting at the FIRST column of `range`.

        Google appends after the last "table" it detects inside the range, starting at
        that table's first column: on `Sheet!A:R`, a block of data starting at column R
        sends the new row to R…AI. The call is therefore anchored on the first column
        of the range (`Sheet!A:A`), whose only table starts in that column; the
        written range is then checked, and a row written elsewhere raises.
        """
        anchor, column = _append_anchor(range)
        result = self.sheets.values().append(
            spreadsheetId=spreadsheet_id,
            range=anchor,
            valueInputOption=value_input,
            insertDataOption='INSERT_ROWS',
            body={'values': values},
        ).execute()
        updates = result.get('updates', {})
        written = updates.get('updatedRange')
        if written and _first_column(written) != column:
            raise SheetsClientError(
                f"append landed at {written}, not in column {column} as requested "
                f"by {range!r}: the row is written there and must be moved or deleted")
        return {
            'updated_range': written,
            'updated_rows': updates.get('updatedRows'),
            'updated_cells': updates.get('updatedCells'),
        }

    def clear(self, spreadsheet_id: str, range: str) -> dict:
        """Clear values in a range."""
        result = self.sheets.values().clear(
            spreadsheetId=spreadsheet_id,
            range=range,
            body={},
        ).execute()
        return {'cleared_range': result.get('clearedRange')}

    def write_csv(
        self,
        spreadsheet_id: str,
        csv_path: str,
        sheet_name: Optional[str] = None,
    ) -> dict:
        """Write a CSV file to a sheet (clears then writes)."""
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            values = list(reader)

        # Resolve actual sheet name if not provided
        if not sheet_name:
            meta = self.get_metadata(spreadsheet_id)
            sheet_name = meta['sheets'][0]['title'] if meta['sheets'] else 'Sheet1'

        range_name = f"'{sheet_name}'!A1"
        self.clear(spreadsheet_id, f"'{sheet_name}'")
        return self.write(spreadsheet_id, range_name, values)

    def read_csv(self, spreadsheet_id: str, range: str = 'A:ZZ') -> str:
        """Read a sheet and return as CSV string."""
        rows = self.read(spreadsheet_id, range)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        return output.getvalue()


def _split_range(range: str) -> tuple:
    """`'Sheet'!A1:B2` → (`'Sheet'!`, `A1:B2`) ; a bare sheet name has no cells."""
    sheet, bang, cells = range.rpartition('!')
    if bang:
        return sheet + '!', cells
    if _A1.fullmatch(range):
        return '', range
    return range + '!', ''


def _first_column(range: str) -> str:
    """First column letter(s) of an A1 range ; `A` when the range names none."""
    _, cells = _split_range(range)
    start = cells.split(':')[0]
    letters = ''.join(c for c in start if c.isalpha()).upper()
    return letters or 'A'


def _append_anchor(range: str) -> tuple:
    """The single-column range an append is sent to, and its column."""
    sheet, cells = _split_range(range)
    start = cells.split(':')[0]
    column = _first_column(range)
    row = ''.join(c for c in start if c.isdigit())
    return f"{sheet}{column}{row}:{column}", column

