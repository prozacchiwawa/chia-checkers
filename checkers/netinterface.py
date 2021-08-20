# Documenting the network and wallet interfaces we use so that versions engaged
# with the real infrastructure can follow the same shape.
class WalletInterface:
    async def launch_smart_coin(self, program, amt=None, launcher=None):
        pass

    async def spend_coin(self, coin, push_tx=True, amt=None, args=None):
        pass

class NetInterface:
    def get_height(self):
        pass

    def get_all_block(self, begin_height, end_height):
        pass
