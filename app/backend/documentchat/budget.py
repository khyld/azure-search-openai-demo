import uuid
from decimal import ROUND_CEILING, Decimal

from .lifecycle import DocumentError, utc_now


class ArtifactBudget:
    path = "coordination/artifact-budget.json"
    rate_card = "documentchat-2026-10-v1"

    def __init__(self, store):
        self.store = store

    async def reserve_model(self, model: str, purpose: str) -> str:
        if model != "gpt-5.4-mini":
            raise DocumentError("Modellens prisgrundlag er ikke godkendt.", 429)
        input_tokens, output_tokens = 400_000, 3500
        input_rate, output_rate = Decimal("493.38"), Decimal("2960.28")
        amount = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
        return await self.reserve(self.buffered_ore(amount), purpose)

    async def reserve_translation(self, extraction: dict) -> str:
        characters = sum(len(page["text"].encode("utf-16-le")) // 2 for page in extraction["pages"])
        estimated_characters = 2 * characters + 10_000
        amount = Decimal(estimated_characters) * Decimal("10500") / 1_000_000
        return await self.reserve(self.buffered_ore(amount), "translation")

    def buffered_ore(self, amount: Decimal) -> int:
        price_margin, vat = Decimal("2"), Decimal("1.25")
        return int((amount * price_margin * vat).to_integral_value(rounding=ROUND_CEILING))

    async def reserve(self, amount_ore: int, purpose: str) -> str:
        if type(amount_ore) is not int or amount_ore <= 0 or purpose not in ("summary", "language", "translation"):
            raise ValueError("Invalid budget reservation")
        async with self.store.lock(self.path, create=True) as lease:
            ledger = await self.store.read(self.path)
            if not self.valid(ledger):
                raise DocumentError("Maanedens dokumentbudget er ikke godkendt. Kontakt administratoren.", 429)
            allocated = ledger["overhead_ore"] + sum(item["amount_ore"] for item in ledger["reservations"].values())
            if allocated + amount_ore > ledger["limit_ore"]:
                raise DocumentError("Dokumentbudgettet er opbrugt. Ingen ny betalt behandling er startet.", 429)
            reservation_id = str(uuid.uuid4())
            ledger["reservations"][reservation_id] = {"amount_ore": amount_ore, "purpose": purpose}
            await self.store.write(self.path, ledger, lease)
            return reservation_id

    def valid(self, ledger: dict) -> bool:
        if not isinstance(ledger, dict):
            return False
        if (type(ledger.get("version")) is not int or ledger.get("version") != 1 or ledger.get("month") != utc_now().strftime("%Y-%m")
                or ledger.get("rate_card") != self.rate_card):
            return False
        limit, overhead = ledger.get("limit_ore"), ledger.get("overhead_ore")
        if type(limit) is not int or not 0 < limit <= 100_000 or type(overhead) is not int or not 0 <= overhead <= limit:
            return False
        reservations = ledger.get("reservations")
        return isinstance(reservations, dict) and all(
            isinstance(key, str) and isinstance(item, dict)
            and type(item.get("amount_ore")) is int and item["amount_ore"] > 0
            and item.get("purpose") in ("summary", "language", "translation")
            for key, item in reservations.items()
        )