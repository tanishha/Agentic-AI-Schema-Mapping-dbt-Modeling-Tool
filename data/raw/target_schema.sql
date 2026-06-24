CREATE TABLE customers (
    customer_id       INTEGER PRIMARY KEY,
    full_name         TEXT    NOT NULL,
    date_of_birth     TEXT,
    registration_date TEXT    NOT NULL,
    account_status    TEXT    NOT NULL DEFAULT 'active'
);

CREATE TABLE contact_details (
    contact_id      INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
    email_address   TEXT,
    phone_number    TEXT
);

CREATE TABLE addresses (
    address_id      INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
    street_address  TEXT,
    city            TEXT,
    country_name    TEXT    NOT NULL
);
