def libraries_and_dependencies():
    import pandas as pd, numpy as np, sqlalchemy as sqla, shutil, decouple
    from sqlalchemy import String, Integer, Numeric
    from IPython.display import display

    pd.set_option("display.max_rows", None)
    pd.set_option("display.float_format", "{:.2f}".format)

    engine = sqla.create_engine(decouple.config("MIS_DB"), pool_pre_ping=True)
    red = sqla.create_engine(decouple.config("MIS_DB_LOCAL_DASHBOARD"), pool_pre_ping=True)
    mis_file_path = decouple.config("MIS_FILE_PATH")
    return pd, np, sqla, shutil, decouple, engine, red, mis_file_path

def ytd_sell_in_fetch():
    pd, np, sqla, shutil, decouple, engine,red,mis_file_path = libraries_and_dependencies()
            # Sell-In Query
    # ── 1. Pull raw sales table ────────────────────────────────────────────────────
    or_qb_mtd   = pd.read_sql("SELECT * FROM staging.or_qb_mtd", engine)

    # For testing
    # or_qb_mtd   = pd.read_sql("SELECT * FROM sales.or_qb", engine)

    # ── 2. Pull reference tables ───────────────────────────────────────────────────
    ref_an  = pd.read_sql("SELECT * FROM ref.account_names", engine)
    ref_ad  = pd.read_sql("SELECT * FROM ref.account_details", engine)
    ref_l   = pd.read_sql("SELECT * FROM ref.lead_names", engine)
    ref_pc  = pd.read_sql("SELECT * FROM ref.product_codes", engine)
    ref_pd  = pd.read_sql("SELECT * FROM ref.product_details", engine)
    ref_um  = pd.read_sql("SELECT * FROM ref.product_ums", engine)
    ref_tp  = pd.read_sql("SELECT * FROM ref.target_products", engine)

    ref_l["year"] = ref_l["year"].astype(int)

    # ── sub_tt: reusable NPI target_type lookup (same subquery used in all 3 sections) ──
    sub_tt = (
        ref_tp[ref_tp["target_type"] == "NPI"][["year", "account_chain", "product_code", "target_type"]]
        .drop_duplicates()
        .sort_values("target_type")
        .groupby(["year", "account_chain", "product_code"], sort=False)
        .first()
        .reset_index()
    )

    # ── 3. OUTRIGHT FROM QB ────────────────────────────────────────────────────────

    sales_tbl = or_qb_mtd.copy()
    sales_tbl["id"] = sales_tbl.index

    sales_tbl["year"]  = pd.to_datetime(sales_tbl["date"]).dt.year
    sales_tbl["month"] = pd.to_datetime(sales_tbl["date"]).dt.strftime("%B")

    join_cte = sales_tbl \
        .merge(ref_an, on="name", how="left") \
        .merge(ref_ad, on="account_name", how="left") \
        .merge(ref_l,  on=["lead_id", "year", "month"], how="left") \
        .merge(ref_pc, on="item", how="left") \
        .merge(ref_pd, left_on="product_code", right_on="product_code1", how="left") \
        .merge(sub_tt, on=["year", "account_chain", "product_code"], how="left", suffixes=("", "_tt"))

    # COALESCE(sub_tt.target_type, ref_pd.target_type)
    join_cte["target_type"] = join_cte["target_type_tt"].combine_first(join_cte["target_type"])
    join_cte = join_cte.drop(columns=["target_type_tt"])

    # um_key variants
    join_cte["account_name"]  = join_cte["account_name"].fillna("")
    join_cte["product_code"]  = join_cte["product_code"].fillna("")
    join_cte["brand"]         = join_cte["brand"].fillna("")
    join_cte["um"]            = join_cte["um"].fillna("")

    u1 = join_cte.copy(); u1["um_key"] = u1["account_name"] + "-" + u1["product_code"] + "-" + u1["brand"] + "-" + u1["um"]; u1["key_order"] = 1
    u2 = join_cte.copy(); u2["um_key"] = u2["account_name"] + "-" + u2["brand"] + "-" + u2["um"];                             u2["key_order"] = 2
    u3 = join_cte.copy(); u3["um_key"] = u3["product_code"] + "-" + u3["brand"] + "-" + u3["um"];                             u3["key_order"] = 3
    u4 = join_cte.copy(); u4["um_key"] = u4["brand"] + "-" + u4["um"];                                                        u4["key_order"] = 4

    union_cte = pd.concat([u1, u2, u3, u4], ignore_index=True)

    partition_cte = union_cte \
        .merge(ref_um[["um_key", "um_multiplier"]], on="um_key", how="left")

    partition_cte = partition_cte[partition_cte["um_key"].notna() & partition_cte["um_multiplier"].notna()]
    partition_cte = partition_cte.sort_values(["id", "key_order"])
    partition_cte = partition_cte.groupby("id", sort=False).first().reset_index()

    out_cols = [
        "date", "year", "month", "num", "po_num", "inventory_site", "account_name", "account_chain",
        "ship_to_address_1", "ship_to_address_2", "rep", "sales_group", "lead_name", "bpc_region",
        "account_channel", "channel_class", "business_unit", "account_type", "item", "product_name",
        "product_code", "main_code", "brand", "product_class", "usage", "product_type", "product_category",
        "target_type", "um", "qty", "amount", "net_amount", "um_multiplier"
    ]

    or_result = partition_cte[out_cols].copy()
    or_result["type"]    = "Sell-In"
    or_result["qty_pcs"] = or_result["qty"] * or_result["um_multiplier"]
    or_result = or_result.drop(columns=["um_multiplier"])
    or_result = or_result.rename(columns={"usage": "product_usage"})

    # ── 4. FINAL RESULT ────────────────────────────────────────────────────────────
    final_cols = [
        "date", "year", "month", "type", "num", "po_num", "inventory_site", "account_name",
        "account_chain", "ship_to_address_1", "ship_to_address_2", "rep", "sales_group", "lead_name",
        "bpc_region", "account_channel", "channel_class", "business_unit", "account_type", "item",
        "product_name", "product_code", "main_code", "brand", "product_class", "product_usage", "product_type",
        "product_category", "target_type", "um", "qty", "qty_pcs", "amount", "net_amount"
    ]

    sellin_df = or_result[final_cols].copy()

    sellin_df["date"] = pd.to_datetime(sellin_df["date"]).dt.date

    print(sellin_df.head(1))
    return sellin_df

def sellin_select_column():
    pd, np, sqla, shutil, decouple, engine,red,mis_file_path = libraries_and_dependencies()
    sellin_df = ytd_sell_in_fetch()
    sin = sellin_df[["year", "month","sales_group","account_name","account_chain","type","lead_name", "product_code","main_code","product_name","product_class","brand","product_type","net_amount"]]
    sin = sin.rename(columns={"type":"account_type"})
# sin = sin.rename(columns={
#     "year":"Year","month":"Month","sales_group":"Sales Group","account_name":"Account Name","account_chain":"Account Chain","type":"Account Type","lead_name":"Lead Name",
#     "product_code":"Product Code","product_name":"Product Name", "product_class":"Class","brand":"Brand","product_type":"Type","net_amount":"Net Amount"})

    sin = sin[sin["year"] > 2023].reset_index(drop=True)
    sin["sales_group"] = sin["sales_group"].fillna("Unknown")
    sin.head()
    return sin

def antijoin():
    pd, np, sqla, shutil, decouple, engine,red,mis_file_path = libraries_and_dependencies()
    sin = sellin_select_column()

    try:
        existing = pd.read_sql('SELECT * FROM `dashboard-sales`.sell_in', con = red)

        for df in [sin, existing]:
            df["year"] = df["year"].astype("int64")
            df["net_amount"] = pd.to_numeric(df["net_amount"], errors="coerce").round(5)

        key_cols = list(sin.columns)
        sin["__occ"] = sin.groupby(key_cols).cumcount()
        existing["__occ"] = existing.groupby(key_cols).cumcount()

        merged = sin.merge(existing, on=key_cols + ["__occ"], how="left", indicator=True)
        new_rows = merged[merged["_merge"] == "left_only"].drop(columns=["_merge", "__occ"])
        sin = sin.drop(columns=["__occ"])

    except Exception:
        new_rows = sin
    print(f"New rows to insert: {len(new_rows)}")
    return new_rows

def insert_to_database(table_name,connection,table_schema,table_if_exist,indexing,chunksize,method):
    pd, np, sqla, shutil, decouple, engine,red,mis_file_path = libraries_and_dependencies()
    new_rows = antijoin()
    try:
        new_rows.to_sql(
        name=table_name,
        con=connection,
        schema=table_schema,
        if_exists=table_if_exist,
        index=indexing,
        chunksize=chunksize,
        method=method
    )
    except Exception as e:
        root = e
        while root.__cause__ is not None:
            root = root.__cause__
        orig = getattr(root, "orig", root)
        print(type(orig).__name__)
        print(str(orig))

