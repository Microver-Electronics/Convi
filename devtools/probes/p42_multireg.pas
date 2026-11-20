procedure Body;
var D, B, It, O: Variant; R: TCoordRect; S: String;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  B := PCBServer.GetPCBBoardByPath('%OUT%\batch\board\board.PcbDoc');
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eRegionObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    if (O.Layer = eMultiLayer) or (O.Kind <> 0) then
    begin
      R := O.BoundingRectangle;
      S := 'layer=' + Layer2String(O.Layer) + ' kind=' + IntToStr(O.Kind) + ' bbox mil=' +
        IntToStr(CoordToMils(R.Left - B.XOrigin)) + ',' + IntToStr(CoordToMils(R.Bottom - B.YOrigin)) + ' - ' +
        IntToStr(CoordToMils(R.Right - B.XOrigin)) + ',' + IntToStr(CoordToMils(R.Top - B.YOrigin));
      if O.InComponent then S := S + ' comp=' + O.Component.Name.Text;
      if O.InPolygon then S := S + ' inpoly';
      if O.Net <> Nil then S := S + ' net=' + O.Net.Name;
      S := S + ' holes=' + IntToStr(O.HoleCount) + ' name=' + O.Name;
      L(S);
    end;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;
