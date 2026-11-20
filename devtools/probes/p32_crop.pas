procedure Body;
var D, S, It, O: Variant; Del: TInterfaceList; i: Integer; R: TCoordRect; X1, Y1, X2, Y2: Integer;
begin
  D := Client.OpenDocument('SCH', '%OUT%\preview\u504_preview.SchDoc');
  Client.ShowDocument(D);
  S := SchServer.GetSchDocumentByPath('%OUT%\preview\u504_preview.SchDoc');
  X1 := MilsToCoord(7900); X2 := MilsToCoord(10300); Y1 := MilsToCoord(8900); Y2 := MilsToCoord(10500);
  Del := TInterfaceList.Create;
  It := S.SchIterator_Create;
  It.SetState_IterationDepth(eIterateFirstLevel);
  O := It.FirstSchObject;
  while O <> Nil do
  begin
    R := O.BoundingRectangle;
    if (R.Right < X1) or (R.Left > X2) or (R.Top < Y1) or (R.Bottom > Y2) then Del.Add(O);
    O := It.NextSchObject;
  end;
  S.SchIterator_Destroy(It);
  for i := 0 to Del.Count - 1 do S.RemoveSchObject(Del.Items[i]);
  S.BorderOn := False;
  S.TitleBlockOn := False;
  S.GraphicallyInvalidate;
  ResetParameters; AddStringParameter('Action', 'All'); RunProcess('Sch:Zoom');
  L('kept around U504, removed ' + IntToStr(Del.Count));
end;
