-- Quote images: each server's default style (bits: 1 colour avatar, 2 avatar on the right, 4 light, 8 tinted).
ALTER TABLE quotes.settings ADD COLUMN default_bits INTEGER NOT NULL DEFAULT 0 CHECK (default_bits BETWEEN 0 AND 15);
