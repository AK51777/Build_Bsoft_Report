ALTER TABLE corpus_block ADD COLUMN content_format TEXT NOT NULL DEFAULT 'plain_text'
  CHECK (content_format IN ('plain_text','structured_json','ooxml_fragment'));
ALTER TABLE corpus_block ADD COLUMN content_payload_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE corpus_block ADD COLUMN asset_manifest_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE corpus_block ADD COLUMN visible_text_hash TEXT NOT NULL DEFAULT '';

UPDATE corpus_block SET visible_text_hash=text_hash WHERE visible_text_hash='';
