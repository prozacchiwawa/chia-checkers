import json
import sqlite3
import binascii

# An object that keeps track of the game state we can see in the blockchain.
# Using the actual arguments (third argument to standard spend), we put in our
# assumptions about the game state and the move we intend to take, as an alist.
# the arguments are matched in the coin solutions.
class GameRecords:
    def run_db(self,stmt,*params):
        cursor = self.db.cursor()
        cursor.execute(stmt, *params)
        cursor.close()
        self.db.commit()

    def __init__(self,cblock,netname,mover,client):
        self.blocks_ago = cblock
        self.netname = netname
        self.client = client
        self.mover = mover

        self.db = sqlite3.connect('checkers.db')
        self.run_db("create table if not exists height (net text primary key, block integer)")
        self.run_db("create table if not exists checkers (launcher text, board text, coin text)")
        self.run_db("create table if not exists self (puzzle_hash)")
        self.db.commit()

    def close(self):
        self.db.close()

    def get_coin_for_launcher(self,launcher):
        """
        Find a coin and board state corresponding to the game launched from the
        named coin.  This wallet recognizes only one game spawned from each coin
        although it's possible to construct a spend that creates multiple of them.
        """
        result = None
        cursor = self.db.cursor()
        print(f'find launcher {launcher}')
        rows = cursor.execute('select coin, board from checkers where launcher = ? limit 1', (launcher,))
        for r in rows:
            print(f'found {r}')
            result = binascii.unhexlify(r[0]), json.loads(r[1])
        cursor.close()

        return result

    def remember_coin(self,launcher,coin,board):
        self.run_db('delete from checkers where launcher = ?', (launcher,))
        self.run_db('insert into checkers (launcher, coin, board) values (?,?,?)', (launcher, binascii.hexlify(coin), json.dumps(board)))

    async def get_current_height_from_node(self):
        """
        Use RPC to get the current blockchain height.
        """
        blockchain_state = await self.client.get_blockchain_state()
        new_height = blockchain_state['peak'].height
        return new_height

    async def retrieve_current_block(self):
        """
        Report our idea of the current block.
        """
        cursor = self.db.cursor()
        current_block = None

        for row in cursor.execute("select block from height where net = ? order by block desc limit 1", (self.netname,)):
            current_block = row[0]

        cursor.close()

        if current_block is None:
            current_block = await self.get_current_height_from_node()
            current_block -= self.blocks_ago

        return current_block

    def set_current_block(self,new_height):
        """
        Update our idea of the current block.
        """
        cursor = self.db.cursor()
        cursor.execute("insert or replace into height (net, block) values (?,?)", (self.netname, new_height))
        cursor.close()
        self.db.commit()

    def set_self_hash(self,puzzle_hash):
        """
        We choose an identity to use when playing checkers based on derived keys in
        our gamut as derived by master_sk_to_wallet_sk.  This ientity is used to
        find updates to games as the participant ids are listed in the arguments.
        """
        cursor = self.db.cursor()
        cursor.execute("delete from self")
        cursor.close()
        self.db.commit()

        cursor = self.db.cursor()
        cursor.execute("insert or replace into self (puzzle_hash) values (?)", (puzzle_hash,))
        cursor.close()
        self.db.commit()

    def get_self_hash(self):
        """
        Get our identity for finding games we're playing.
        """
        result = None

        cursor = self.db.cursor()
        rows = cursor.execute("select puzzle_hash from self limit 1")
        for r in rows:
            result = r[0]

        cursor.close()

        return result

    async def update_to_current_block(self, blocks_ago):
        """
        Scan forward blocks to find updates involving us.
        """
        current_block = await self.retrieve_current_block()
        new_height = await self.get_current_height_from_node()
        if new_height - blocks_ago < current_block:
            current_block = max(new_height - blocks_ago, 1)

        if current_block is None:
            current_block = await self.get_current_height_from_node()
            current_block -= self.blocks_ago

        while new_height > current_block:
            if new_height - current_block > 1:
                new_height = current_block + 1

            print(f'absorb state until block {new_height}')
            await self.mover.absorb_state(new_height, self.client)
            self.set_current_block(new_height)
            current_block = new_height
            blockchain_state = await self.client.get_blockchain_state()
            new_height = blockchain_state['peak'].height
