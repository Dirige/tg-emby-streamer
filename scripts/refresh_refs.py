import asyncio
import logging
import sqlite3
from app.telegram.client import get_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('refresh_refs')

async def refresh_file_references():
    client = await get_client()
    conn = sqlite3.connect('/app/data/media.db')
    c = conn.cursor()
    c.execute('SELECT id, chat_id, message_id, file_id FROM media WHERE file_id IS NOT NULL')
    rows = c.fetchall()
    logger.info('Total records: %d' % len(rows))

    count = 0
    errors = 0
    for row in rows:
        mid, chat_id, msg_id, old_file_id = row
        try:
            msg = await client.get_messages(int(chat_id), msg_id)
            if msg and (msg.video or msg.document):
                media = msg.video or msg.document
                new_file_id = media.file_id
                c.execute('UPDATE media SET file_id=?, file_unique_id=? WHERE id=?',
                    (new_file_id, media.file_unique_id, mid))
                count += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning('Error on msg %d: %s' % (msg_id, str(e)[:80]))
        
        if count % 500 == 0 and count > 0:
            conn.commit()
            logger.info('Progress: %d/%d' % (count, len(rows)))
        await asyncio.sleep(0.1)

    conn.commit()
    conn.close()
    logger.info('Done: %d refreshed, %d errors' % (count, errors))

asyncio.run(refresh_file_references())
