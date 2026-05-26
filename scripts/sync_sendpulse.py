import sys
import os
import argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.api.core.sendpulse_sync import (
    sync_all_clients_to_sendpulse,
    sync_all_users_to_sendpulse,
    sync_all_contacts_to_sendpulse,
)
from app.api.core.logger import logger
def main():
    parser = argparse.ArgumentParser(description='Sync contacts to SendPulse')
    parser.add_argument(
        '--type',
        type=str,
        default='all',
        choices=['clients', 'users', 'all'],
        help='Type of contacts to sync (default: all)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Maximum number of contacts to sync (optional)'
    )
    parser.add_argument(
        '--address-book-id',
        type=int,
        default=None,
        help='SendPulse address book ID (optional)'
    )
    args = parser.parse_args()
    try:
        logger.info(f"Starting SendPulse sync: type={args.type}, limit={args.limit}")
        if args.type == 'clients':
            results = sync_all_clients_to_sendpulse(
                address_book_id=args.address_book_id,
                limit=args.limit,
            )
            print(f"Clients sync completed: {results}")
        elif args.type == 'users':
            results = sync_all_users_to_sendpulse(
                address_book_id=args.address_book_id,
                limit=args.limit,
            )
            print(f"Users sync completed: {results}")
        else:  
            results = sync_all_contacts_to_sendpulse(
                address_book_id=args.address_book_id,
                limit=args.limit,
            )
            print(f"All contacts sync completed:")
            print(f"  Clients: {results['clients']}")
            print(f"  Users: {results['users']}")
            print(f"  Total: {results['total']}")
        logger.info("SendPulse sync completed successfully")
        return 0
    except Exception as e:
        logger.error(f"Error during SendPulse sync: {e}", exc_info=True)
        return 1
if __name__ == "__main__":
    exit(main())
