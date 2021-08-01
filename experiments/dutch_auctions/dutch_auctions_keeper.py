""" Dutch Auctions Keeper Module
    Extension of the Keeper module which accomodates the new
    Dutch auction liquidation system.
"""

from pydss.keeper import VaultKeeper
from pydss.pymaker.numeric import Wad, Rad, Ray
from experiments.dutch_auctions.clip import Sale


class ClipperKeeper(VaultKeeper):
    """
    clippers = {str: Clipper}
    vat = Vat
    """

    def __init__(self, vat, dai_join, ilks, uniswap):
        """ ilks = [{"ilk_id": ..., "clipper": ...}]
        """

        self.clippers = {}
        for ilk in ilks:
            self.clippers[ilk["ilk_id"]] = ilk["clipper"]

        super().__init__(vat, dai_join, ilks, uniswap)


class ClipperBidder(ClipperKeeper):
    """
    clippers = {str: Clipper}
    vat = Vat
    gas_oracle = GasOracle
    """

    def __init__(self, vat, dai_join, ilks, uniswap, gas_oracle=None):
        self.gas_oracle = gas_oracle

        super().__init__(vat, dai_join, ilks, uniswap)

    def generate_actions_for_timestep(self, t):
        actions = []
        self.open_max_vaults(actions)
        self.find_and_take_sales(t, actions)
        return actions

    def find_and_take_sales(self, t, actions):
        sales_to_take = self.find_sales_to_take(t)
        for ilk_id in sales_to_take:
            for sale in sales_to_take[ilk_id]:
                stance = self.run_bidding_model(sale, ilk_id, t)
                if stance["amt"] > Wad(0):
                    actions.append(
                        {
                            "key": "TAKE",
                            "keeper": self,
                            "handler": self.clippers[ilk_id].take,
                            "args": [
                                sale.id,
                                stance["amt"],
                                stance["max_price"],
                                stance["who"],
                                stance["data"],
                                t,
                                self.ADDRESS,
                            ],
                            "kwargs": {},
                        }
                    )

    def find_sales_to_take(self, t):
        raise NotImplementedError

    def run_bidding_model(self, sale, ilk_id, t):
        raise NotImplementedError


class NaiveClipperKeeper(ClipperBidder):
    """ Takes a sale when its price is below this keeper's desired discount.
    """

    def __init__(self, vat, dai_join, ilks, uniswap, gas_oracle=None):
        self.desired_discounts = {}
        for ilk in ilks:
            self.desired_discounts[ilk["ilk_id"]] = ilk["desired_discount"]

        super().__init__(vat, dai_join, ilks, uniswap, gas_oracle)

    def is_profitable(self, sale, ilk_id, t, threshold=Rad(0)):
        weth_clip = self.clippers["WETH"]
        weth_pip = weth_clip.spotter.ilks["WETH"].pip

        weth_val = weth_pip.peek(t)
        # TODO: Pull from Gaussian
        gas_limit = Wad.from_number(300000)
        # Gas price denominated in DAI/gas unit
        gas_price = (
            self.gas_oracle.peek(t)
            * Wad.from_number(10 ** -18)
            * (weth_val / Wad(weth_clip.spotter.par))
        )
        expected_gas = Rad(gas_limit * gas_price)

        desired_slice = self.run_bidding_model(sale, ilk_id, t)["amt"]
        expected_dai = self.uniswap.get_slippage(
            "0xa478c2975ab1ea89e8196811f51a7b7ade33eb11", "WETH", desired_slice, t
        )[0]

        profit = expected_dai - expected_gas

        return profit > threshold

    def find_sales_to_take(self, t):
        sales_to_take = {}
        for ilk_id in self.ilks:
            clipper = self.clippers[ilk_id]
            des_disc = self.desired_discounts[ilk_id]
            for sale in clipper.sales.values():
                done, price = clipper.status(sale.tic, sale.top, t)
                pip = clipper.spotter.ilks[ilk_id].pip
                val = pip.peek(t)
                is_profitable = self.is_profitable(sale, ilk_id, t)
                if (
                    not done
                    and price <= Ray(val / Wad(clipper.spotter.par)) * des_disc
                    and is_profitable
                ):
                    if not sales_to_take.get(ilk_id):
                        sales_to_take[ilk_id] = []
                    sales_to_take[ilk_id].append(sale)

        return sales_to_take

    def run_bidding_model(self, sale, ilk_id, t):
        stance = {}

        clipper = self.clippers[ilk_id]
        pip = clipper.spotter.ilks[ilk_id].pip
        val = pip.peek(t)
        max_price = Ray(val / Wad(clipper.spotter.par)) * self.desired_discounts[ilk_id]
        dai = self.vat.dai.get(self.ADDRESS, Rad(0))
        desired_amt_dai = Rad(sale.lot * max_price)
        if desired_amt_dai <= dai:
            amt = sale.lot
        else:
            amt = Wad(dai / Rad(max_price))
        stance["max_price"] = max_price
        stance["amt"] = amt
        stance["who"] = self.ADDRESS
        stance["data"] = []

        return stance


class IncentivizedKeeper(ClipperKeeper):
    def __init__(self, vat, dai_join, ilks, uniswap, gas_oracle):
        self.gas_oracle = gas_oracle
        super().__init__(vat, dai_join, ilks, uniswap)

    def is_profitable(self, sale, ilk_id, t, threshold=Rad(0)):
        weth_clip = self.clippers["WETH"]
        weth_pip = weth_clip.spotter.ilks["WETH"].pip
        ilk_clip = self.clippers[ilk_id]

        weth_val = weth_pip.peek(t)
        gas_limit = Wad.from_number(300000)
        # Gas price denominated in DAI/gas unit
        gas_price = (
            self.gas_oracle.peek(t)
            * Wad.from_number(10 ** -18)
            * (weth_val / Wad(weth_clip.spotter.par))
        )
        expected_gas = Rad(gas_limit * gas_price)

        expected_incentive = ilk_clip.tip + sale.tab * Rad(ilk_clip.chip)

        profit = expected_incentive - expected_gas

        return profit > threshold


class BarkKeeper(IncentivizedKeeper):
    """
    dog = Dog
    vat = Vat
    gas_oracle = GasOracle
    """

    def __init__(self, vat, dai_join, ilks, uniswap, gas_oracle, dog):
        self.dog = dog

        super().__init__(vat, dai_join, ilks, uniswap, gas_oracle)

    def calculate_tab(self, ilk_id, urn_id):
        art = self.vat.urns[ilk_id][urn_id].art
        milk = self.dog.ilks[ilk_id]

        rate = self.vat.ilks[ilk_id].rate
        dust = self.vat.ilks[ilk_id].dust

        room = min(self.dog.Hole - self.dog.Dirt, milk.hole - milk.dirt)

        dart = min(art, Wad(room / Rad(rate)) / milk.chop)

        if Rad(rate * (art - dart)) < dust:
            # Q: What if art > room?
            # Resetting dart = art here can push past liq limit
            dart = art

        due = Rad(rate * dart)

        tab = due * Rad(milk.chop)

        return tab

    def generate_actions_for_timestep(self, t):
        # TODO: also include bid-placing actions
        actions = []
        self.open_max_vaults(actions)
        for ilk_id in self.ilks:
            ilk = self.vat.ilks[ilk_id]
            for urn in self.vat.urns[ilk_id].values():
                is_unsafe = Rad(urn.ink * ilk.spot) < Rad(urn.art * ilk.rate)
                # call calculate_tab, create fake Sale object, pass into is_profitable
                expected_tab = self.calculate_tab(ilk_id, urn.ADDRESS)
                fake_sale = Sale(None, expected_tab, None, None, None, None, None,)
                is_profitable = self.is_profitable(fake_sale, ilk_id, t)
                if is_unsafe and is_profitable:
                    actions.append(
                        {
                            "key": "BARK",
                            "keeper": self,
                            "handler": self.dog.bark,
                            "args": [ilk_id, urn.ADDRESS, self.ADDRESS, t],
                            "kwargs": {},
                        }
                    )
        return actions


class RedoKeeper(IncentivizedKeeper):
    """
    clippers = {str: Clipper}
    vat = Vat
    """

    def generate_actions_for_timestep(self, t):
        actions = []
        self.open_max_vaults(actions)
        for ilk_id in self.ilks:
            clipper = self.clippers[ilk_id]
            for sale in clipper.sales.values():
                done, _ = clipper.status(sale.tic, sale.top, t)
                is_profitable = self.is_profitable(sale, ilk_id, t)
                if done and is_profitable:
                    actions.append(
                        {
                            "key": "REDO",
                            "keeper": self,
                            "handler": self.clippers[ilk_id].redo,
                            "args": [sale.id, self.ADDRESS, t],
                            "kwargs": {},
                            "extra": {"ilk_id": ilk_id,},
                        }
                    )

        return actions
