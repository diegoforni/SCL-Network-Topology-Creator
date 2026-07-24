/**
 * ensure_transfer_demo.js — idempotently prepares the DB + on-chain state so the
 * app's SFT transfer endpoint (POST /api/transfers) works out of the box in a
 * repo-server host. Run after `npm run seed` (tables + admin user exist).
 *
 * What it does (all idempotent / findOrCreate):
 *   1. Ensures two demo centers exist, each with a fixed blockchain_contract_id
 *      (a Stellar address; these persist on testnet across host restarts).
 *   2. Ensures one demo item, token_status='minted', sitting at Centro Norte.
 *   3. Mints the item's tokenId to Centro Norte's address ON-CHAIN (only if its
 *      balance is currently 0), using the admin keypair (contract admin).
 *
 * After this, POST /api/transfers {item_id, from_center_id, to_center_id, quantity}
 * performs a real Soroban SFT transfer.
 */
require('dotenv').config();
const StellarSdk = require('@stellar/stellar-sdk');
const { sequelize, Center, Item, Category, User } = require('../src/models');
const stellarService = require('../src/services/blockchain/stellarService');
const sftService = require('../src/services/blockchain/sftService');

// Fixed demo center addresses (public keys only — they never sign; the admin
// keypair mints/transfers on their behalf). Baked so on-chain balances persist.
const CENTER_A_ADDR = 'GBLRPJY44AIYP7E67G2IGA6C3TG4RFI3KAETVXHPETXMWI4MC34M3SOD';
const CENTER_B_ADDR = 'GCV6J4VENV5FACBVPE2474ZBVSLHJN22DBG662635PBPKKTBUMMSOOPJ';
const DEMO_QTY = 10;

async function main() {
  await sequelize.authenticate();
  await sequelize.sync();

  if (!sftService.isEnabled) {
    console.log('[demo] SFT not enabled — skipping blockchain demo setup.');
    return;
  }
  if (!stellarService.keypair) {
    stellarService.keypair = StellarSdk.Keypair.fromSecret(process.env.STELLAR_SECRET_KEY);
  }

  const admin = await User.findOne({ where: { username: 'admin' } });
  if (!admin) throw new Error('admin user missing — run `npm run seed` first');

  let category = await Category.findOne();
  if (!category) category = await Category.create({ name: 'Demo', is_active: true });

  const [centerA] = await Center.findOrCreate({
    where: { name: 'Centro Norte (demo)' },
    defaults: { blockchain_contract_id: CENTER_A_ADDR, is_active: true, created_by: admin.id, geo_hash: 'demo-norte' },
  });
  const [centerB] = await Center.findOrCreate({
    where: { name: 'Centro Sur (demo)' },
    defaults: { blockchain_contract_id: CENTER_B_ADDR, is_active: true, created_by: admin.id, geo_hash: 'demo-sur' },
  });

  const [item] = await Item.findOrCreate({
    where: { name: 'Kit Alimentos (demo tokenizado)' },
    defaults: { category_id: category.id, quantity: DEMO_QTY, token_status: 'minted', current_center_id: centerA.id, is_active: true },
  });
  if (item.token_status !== 'minted' || item.current_center_id !== centerA.id) {
    item.token_status = 'minted';
    item.current_center_id = centerA.id;
    if (item.quantity < 1) item.quantity = DEMO_QTY;
    await item.save();
  }

  // Mint the item's token to Centro Norte on-chain if its balance is 0.
  const tokenId = sftService.computeTokenId(item.id);
  const tokSc = sftService._hexToBytesScVal(tokenId);
  const aSc = sftService._addressToScVal(CENTER_A_ADDR);

  let balance = 0;
  try {
    const r = await stellarService._invocarContrato(sftService.sftContractId, 'balance_of', [aSc, tokSc], { readOnly: true });
    balance = Number(r.returnValue);
  } catch (e) {
    console.log('[demo] balance_of read failed (continuing to mint):', String(e.message).slice(0, 120));
  }

  if (balance === 0) {
    const metaSc = sftService._metadataToScVal({ item_id: item.id, nombre: item.name, categoria: 'demo', attributes_hash: '0'.repeat(64) });
    const qtySc = StellarSdk.nativeToScVal(DEMO_QTY, { type: 'u64' });
    const zeroHash = sftService._hexToBytesScVal('0'.repeat(64));
    const r = await stellarService._invocarContrato(sftService.sftContractId, 'mint', [aSc, tokSc, metaSc, qtySc, zeroHash]);
    console.log('[demo] minted', DEMO_QTY, 'demo tokens to Centro Norte (tx:', (r.txId || '').slice(0, 12) + '...)');
  } else {
    console.log('[demo] Centro Norte already holds', balance, 'demo tokens on-chain');
  }

  console.log('[demo] ✅ TRANSFER READY → POST /api/transfers ' +
    JSON.stringify({ item_id: item.id, from_center_id: centerA.id, to_center_id: centerB.id, quantity: 1 }));
  await sequelize.close();
}

main().catch((e) => { console.error('[demo] error:', e.message); process.exit(1); });
