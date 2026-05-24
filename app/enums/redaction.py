from enum import StrEnum


class SensitiveDataType(StrEnum):
    IBAN = "IBAN"
    PESEL = "PESEL"
    NIP = "NIP"
    REGON = "REGON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    DATE = "DATE"
    POSTAL_CODE = "POSTAL_CODE"
    ID_NUMBER = "ID_NUMBER"
    PASSPORT_NUMBER = "PASSPORT_NUMBER"
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    ADDRESS = "ADDRESS"
    CONTACT = "CONTACT"
