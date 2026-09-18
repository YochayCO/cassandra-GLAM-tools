CREATE OR REPLACE FUNCTION AddCategory(title varchar(255),subcats int ,files int,_to varchar(255),_level int)
RETURNS void AS $$
DECLARE
t varchar(255)[];
l int[];
BEGIN
IF EXISTS (SELECT 1 FROM categories WHERE page_title = title) THEN
UPDATE categories SET cl_to=cl_to||_to,cat_level=cat_level||_level WHERE page_title=title;
ELSE
t[0]:=_to;
l[0]:=_level;
INSERT INTO categories(page_title, cat_subcats, cat_files, cl_to, cat_level) VALUES(title,subcats,files,t,l);
END IF;
EXCEPTION
    WHEN others THEN
        RAISE NOTICE 'Error on %',title;
END;
$$ LANGUAGE plpgsql;
