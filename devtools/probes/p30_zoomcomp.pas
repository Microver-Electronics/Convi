procedure Body;
var S, It, C: Variant;
begin
  S := SchServer.GetCurrentSchDocument;
  It := S.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while C <> Nil do
  begin
    if (C.Designator.Text = 'U504') then
    begin
      C.Selection := True;
      L('selected U504 at ' + IntToStr(CoordToMils(C.Location.X)) + ',' + IntToStr(CoordToMils(C.Location.Y)));
    end
    else C.Selection := False;
    C := It.NextSchObject;
  end;
  S.SchIterator_Destroy(It);
  S.GraphicallyInvalidate;
  ResetParameters; AddStringParameter('Action', 'Selected'); RunProcess('Sch:Zoom');
  ResetParameters; AddStringParameter('Action', 'In'); RunProcess('Sch:Zoom');
  L('zoomed');
end;
