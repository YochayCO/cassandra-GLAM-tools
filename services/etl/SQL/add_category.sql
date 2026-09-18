CREATE OR REPLACE FUNCTION AddCategory(title varchar(255),subcats int ,files int,_to varchar(255),_level int)
RETURNS void AS $$
DECLARE
t varchar(255)[];
l int[];
BEGIN
IF EXISTS (SELECT 1 FROM categories WHERE page_title = title) THEN
t[0]:=_to;
l[0]:=_level;
UPDATE categories SET cat_subcats=subcats, cat_files=files, cl_to=t, cat_level=l WHERE page_title=title;
ELSE
t[0]:=_to;
l[0]:=_level;
INSERT INTO categories(page_title, cat_subcats, cat_files, cl_to, cat_level, last_crawled_at) VALUES(title,subcats,files,t,l,NOW());
END IF;
EXCEPTION
    WHEN others THEN
        RAISE NOTICE 'Error on %',title;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION AddImage(_name varchar(255),_user varchar(255) ,_timestamp timestamp without time zone,size bigint,_to varchar(255))
RETURNS void AS $$
DECLARE
t varchar(255)[];
opened boolean;
BEGIN
SELECT is_alive into opened FROM images WHERE img_name = _name;
t[0]:=_to;
IF FOUND THEN
if opened then
UPDATE images SET cl_to=array_remove(cl_to,_to)||_to WHERE img_name=_name;
else
UPDATE images SET cl_to=t, is_alive=true WHERE img_name=_name;
end if;
ELSE
INSERT INTO images(img_name,img_user_text,img_timestamp,img_size,cl_to,is_alive) VALUES(_name,_user,_timestamp,size,t,true);
END IF;
EXCEPTION
    WHEN others THEN
        RAISE NOTICE 'Error on %',_name;
END;
$$ LANGUAGE plpgsql;
